"""Private email presentation; never render the sender's HTML."""
import re
import time
from html.parser import HTMLParser
from urllib.parse import urlsplit

import requests

# Exact image origin used by SFPUC's email provider. Additional origins need review.
IMAGE_HOSTS = frozenset({"d31hzlhk6di2h5.cloudfront.net"})
MAX_IMAGES = 12
MAX_IMAGE_BYTES = 3 * 1024 * 1024


def image_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return None
    if "\\" in value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        return None
    try:
        url = urlsplit(value)
    except ValueError:
        return None
    if url.scheme != "https" or url.netloc.lower() not in IMAGE_HOSTS or not url.path.startswith("/"):
        return None
    return url._replace(fragment="").geturl()


class EmailImages(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head", "iframe", "object", "template", "svg"}:
            self.hidden += 1
        if tag != "img" or self.hidden or len(self.images) >= MAX_IMAGES:
            return
        attrs = dict(attrs)
        src = image_url(attrs.get("src"))
        if not src or any(image["url"] == src for image in self.images):
            return
        style = attrs.get("style") or ""
        if "hidden" in attrs or re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", style, re.I):
            return
        dimensions = []
        for axis in ("width", "height"):
            values = [attrs.get(axis) or ""] + re.findall(rf"(?:^|;)\s*{axis}\s*:\s*([^;]+)", style, re.I)
            for value in values:
                match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*(?:!important)?\s*", value, re.I)
                if match:
                    dimensions.append(float(match[1]))
        if any(size <= 2 for size in dimensions):
            return
        self.images.append({"url": src, "alt": (attrs.get("alt") or "Email image")[:300],
                            "small": bool(dimensions) and max(dimensions) <= 64})

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head", "iframe", "object", "template", "svg"}:
            self.hidden = max(0, self.hidden - 1)


def images(payload):
    html = payload.get("RawHtmlBody") if isinstance(payload, dict) else None
    if not isinstance(html, str) or len(html) > 100_000:
        return []
    parser = EmailImages()
    parser.feed(html)
    return parser.images


def fetch_image(url):
    """Bounded raster-only download from the reviewed origin, with no redirects."""
    if image_url(url) != url:
        raise ValueError("Image origin is not supported.")
    started = time.monotonic()
    # Do not inherit netrc credentials, proxy settings, or browser cookies.
    with requests.Session() as session:
        session.trust_env = False
        with session.get(url, stream=True, allow_redirects=False, timeout=(3, 5),
                         headers={"Accept": "image/png,image/jpeg,image/gif,image/webp"}) as response:
            mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if response.status_code != 200 or mime not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
                raise ValueError("Image is unavailable.")
            if int(response.headers.get("Content-Length", "0")) > MAX_IMAGE_BYTES:
                raise ValueError("Image is too large.")
            content = bytearray()
            for chunk in response.iter_content(64 * 1024):
                content.extend(chunk)
                if len(content) > MAX_IMAGE_BYTES or time.monotonic() - started > 10:
                    raise ValueError("Image limit exceeded.")
    data = bytes(content)
    signatures = {"image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
                  "image/jpeg": data.startswith(b"\xff\xd8\xff"),
                  "image/gif": data.startswith((b"GIF87a", b"GIF89a")),
                  "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP"}
    if not signatures[mime]:
        raise ValueError("Invalid image content.")
    return data, mime


_MARKER = re.compile(r"^\s*(?:-{3,}\s*(?:Forwarded message|Original Message)\s*-{3,}|Begin forwarded message:)\s*$", re.I)
_HEADER = re.compile(r"^(From|Date|Sent|Subject|To|Cc|Bcc|Reply-To):", re.I)


def _unquote(line, depth):
    for _ in range(depth):
        line = re.sub(r"^[ \t]*>[ \t]?", "", line, count=1)
    return line


def clean_forward(title, body):
    """Remove confirmed forwarding envelopes, never guess where a bulletin ends."""
    cleaned, removed = body, 0
    for _ in range(5):
        original_lines = cleaned.splitlines()
        found = False
        for start, line in enumerate(original_lines):
            prefix = re.match(r"^[ \t]*(?:>[ \t]*)*", line)[0]
            depth = prefix.count(">")
            if not _MARKER.fullmatch(_unquote(line, depth)):
                continue
            # Remove only the envelope's quote depth. A bulletin may itself
            # contain greater-than thresholds or intentional quoted passages.
            lines = [_unquote(value, depth) for value in original_lines]
            fields, end = set(), start + 1
            while end < min(len(lines), start + 40):
                current = lines[end]
                header = _HEADER.match(current)
                if header:
                    fields.add(header[1].lower())
                elif not current.strip():
                    if end + 1 < len(lines) and _HEADER.match(lines[end + 1]):
                        end += 1
                        continue
                    break
                elif not fields or not current[:1].isspace():
                    break
                end += 1
            if {"from", "subject"} <= fields and end < len(lines) and not lines[end].strip():
                candidate = "\n".join(lines[end + 1:]).strip()
                if candidate:
                    cleaned, found = candidate, True
                    removed += 1
                    break
        if not found:
            break
    if removed:
        title = re.sub(r"^(?:\s*fw(?:d)?\s*:\s*)+", "", title, flags=re.I).strip()
    return {"title": title, "body": cleaned, "removed": removed}
