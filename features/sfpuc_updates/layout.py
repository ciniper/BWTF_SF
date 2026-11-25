"""A public bulletin projection, never an inbox or raw-payload response.

The tiny tree below only locates forwarding/footer blocks. nh3's HTML5 parser
and allowlist are the security boundary; the result also runs without scripts
in a sandboxed frame. Originals and coordinator reviews remain unchanged.
"""
import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

import nh3

from features.sfpuc_updates import parser, presentation

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
FOOTER = re.compile(r"Manage\s+your preferences|Opt Out\s+using TrueRemove|This email was sent to", re.I)


class Node:
    def __init__(self, tag="", attrs=(), parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []

    def text(self):
        return "".join(c.text() if isinstance(c, Node) else c for c in self.children)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def html(self):
        body = "".join(c.html() if isinstance(c, Node) else escape(c) for c in self.children)
        if not self.tag:
            return body
        attrs = "".join(f' {k}="{escape(v or "", quote=True)}"' for k, v in self.attrs.items())
        return f"<{self.tag}{attrs}>" + ("" if self.tag in VOID else body + f"</{self.tag}>")


class Tree(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack, self.count = [self.root], 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 4000 or len(self.stack) > 80:
            raise ValueError("Email structure is too complex")
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def eligible(row):
    """Recognize this feed, including Gmail forwards; not proof of authorship.

    Setup confirmations, unrelated mail and explicitly rejected messages cannot
    enter the viewer. This check is also used on every public image request.
    """
    subject = re.sub(r"^(?:\s*fw(?:d)?\s*:\s*)+", "", row.get("subject", ""), flags=re.I)
    if not re.match(r"^SF BEACH\b", subject, re.I) or row.get("status") == "rejected":
        return False
    if row.get("sender_matches"):
        return True
    body = row.get("body_text", "")
    return bool(presentation.clean_forward(subject, body)["removed"] and re.search(
        r"(?im)^\s*>?\s*From:[^\n]*\b" + re.escape(parser.EXPECTED_SENDER) + r"\b", body))


def clean_text(title, body):
    result = presentation.clean_forward(title, body)
    # Subscription content is after the bulletin. Keep City contact information.
    result["body"] = FOOTER.split(result["body"], maxsplit=1)[0].strip()
    result["body"] = EMAIL.sub(lambda m: m[0] if m[0].lower() == parser.EXPECTED_SENDER else "", result["body"])
    return result


def _body_tree(html, forwarded):
    root = Tree(html).root
    removed = False
    for _ in range(5):
        header = next((n for n in root.walk() if "gmail_attr" in (n.attrs.get("class") or "").split()
                       and "Forwarded message" in n.text() and "From:" in n.text() and "Subject:" in n.text()), None)
        if header is None:
            break
        container = header.parent
        root = Node()
        root.children = container.children[container.children.index(header) + 1:]
        while root.children and ((isinstance(root.children[0], str) and not root.children[0].strip())
                                 or (isinstance(root.children[0], Node) and root.children[0].tag == "br")):
            root.children.pop(0)
        for child in root.children:
            if isinstance(child, Node):
                child.parent = root
        removed = True
    if forwarded and not removed:
        return None  # Unknown forwarding format: use the clean text, not headers.
    for node in list(root.walk()):
        if not node.parent:
            continue
        style = node.attrs.get("style") or ""
        hidden = "hidden" in node.attrs or re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", style, re.I)
        # Match the smallest footer paragraphs; never remove a parent bulletin table.
        footer = node.tag == "p" and FOOTER.search(node.text())
        if hidden or footer:
            if node in node.parent.children:
                node.parent.children.remove(node)
    return root


def safe_link(value):
    if value and value.lower().startswith("tel:"):
        number = re.sub(r"[\s().-]", "", unquote(value[4:]))
        return "tel:" + number if re.fullmatch(r"\+?\d{7,15}", number) else None
    if not value or len(value) > 2048 or "\\" in value or any(ord(c) <= 32 for c in value):
        return None
    if value.lower() == "mailto:" + parser.EXPECTED_SENDER:
        return value
    try:
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.port not in {None, 443}:
            return None
    except ValueError:
        return None
    # Drop subscriber-management / web-copy URLs even outside recognized footers.
    if url.hostname.endswith("e2ma.net") and not (url.hostname == "t.e2ma.net" and url.path.startswith("/click/")):
        return None
    return value


TAGS = set("a p div span br hr strong b em i u s blockquote ul ol li h1 h2 h3 h4 table tbody thead tfoot tr td th img".split())
STYLES = set("color background-color font-family font-size font-weight font-style text-align text-decoration line-height border-collapse vertical-align padding padding-top padding-bottom padding-left padding-right margin margin-top margin-bottom margin-left margin-right width max-width height".split())


def document(row, image_path):
    """Return sanitized, self-contained layout, or None for plain-text fallback."""
    payload = row.get("raw_payload") or {}
    html = payload.get("RawHtmlBody")
    if not isinstance(html, str) or not html or len(html) > 100_000:
        return None
    clean = presentation.clean_forward(row["subject"], row["body_text"])
    try:
        root = _body_tree(html, bool(clean["removed"]))
        if root is None:
            return None
        image_map = {item["url"]: image_path(i) for i, item in enumerate(presentation.images(payload))}
        for node in list(root.walk()):
            if node.tag == "img" and presentation.image_url(node.attrs.get("src")) not in image_map:
                node.parent.children.remove(node)

        def attribute(tag, attr, value):
            if attr == "src":
                return image_map.get(presentation.image_url(value)) if tag == "img" else None
            if attr == "href":
                return safe_link(value)
            if attr == "style":
                # Only bounded, non-positioned typography and table geometry.
                return ";".join(part for part in value.split(";")
                                if ":" in part and not re.search(r"url|expression|[\\@]|!important|-[0-9]|[0-9]{4}", part, re.I))
            if attr in {"width", "height", "colspan", "rowspan", "cellspacing", "cellpadding", "border"}:
                return value if re.fullmatch(r"\d{1,3}%?", value) else None
            if attr == "align":
                return value if value in {"left", "center", "right"} else None
            return EMAIL.sub(lambda m: m[0] if m[0].lower() == parser.EXPECTED_SENDER else "", value)

        cleaner = nh3.Cleaner(tags=TAGS, clean_content_tags={"script", "style", "head", "iframe", "object", "template", "svg", "math", "form"},
            attributes={"*": {"style"}, "a": {"href", "title"}, "img": {"src", "alt", "width", "height"},
                        "table": {"width", "align", "cellspacing", "cellpadding", "border"},
                        "td": {"width", "height", "align", "colspan", "rowspan"}, "th": {"colspan", "rowspan"}},
            attribute_filter=attribute, filter_style_properties=STYLES,
            set_tag_attribute_values={"a": {"target": "_blank", "referrerpolicy": "no-referrer"}},
            url_schemes={"https", "tel", "mailto"})
        content = cleaner.clean(root.html())
        # The only remaining addresses can be in text (attributes were filtered above).
        content = EMAIL.sub(lambda m: m[0] if m[0].lower() == parser.EXPECTED_SENDER else "", content)
    except (ValueError, RecursionError):
        return None
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="referrer" content="no-referrer">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'self\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            '<style>html,body{box-sizing:border-box;margin:0;padding:0;width:100%;min-width:0;max-width:100%;'
            'background:white;font:15px/1.6 Arial,sans-serif;overflow-wrap:anywhere;overscroll-behavior:none}'
            'html{height:100%;overflow-y:auto;overflow-x:hidden;scrollbar-gutter:stable}body{padding:0 0 20px;min-height:100%;touch-action:pan-y pinch-zoom}'
            '*,*::before,*::after{box-sizing:border-box}table{table-layout:fixed;max-width:100%!important}'
            'div,td,th,p,span,a{overflow-wrap:anywhere}div{max-width:100%!important}td,th{min-width:0}'
            'img{max-width:100%!important;height:auto!important}a{color:#0068a5}a:focus-visible{outline:3px solid #0068a5}'
            '.city-email-meta{padding:0 0 16px}.city-email-meta p{margin:0 0 6px;color:#0068a5;font-size:12px;font-weight:700}'
            '.city-email-meta h1{margin:0;font-size:20px;line-height:1.35;overflow-wrap:anywhere}'
            '@media(max-width:600px){table{width:100%!important}td,th{width:auto!important}'
            '.city-email-meta h1{font-size:18px}}'
            '</style></head><body data-city-email-document>' + content + '</body></html>')
