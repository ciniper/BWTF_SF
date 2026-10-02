"""Small helpers for the forecast's hand-drawn SVG figures: escaping, icon and
logo tiles, label fitting, the house figure CSS and a geometry check.

Copied out of export_how_it_works (whose import loads the whole served model
stack) so a figure can be drawn, tested and previewed without a model; that
report moves onto this module when it is regenerated in P9 (STAGES_DESIGN.md
§1.5). Nothing here reads a file at import: sprite() reads the icon sprite when
it is called.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
ICONS = REPO / "app" / "templates" / "_icons.html"
STATIC = "/static/"            # the app's static root; previews pass a file:// root instead


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def ic(name: str, cls: str = "ic") -> str:
    return f'<svg class="{cls}"><use href="#i-{name}"/></svg>'


def logo_tile(x, y, logo, size=40, static=STATIC) -> str:
    return (f'<rect class="tile logo" x="{x:g}" y="{y:g}" width="{size}" height="{size}" rx="11"/>'
            f'<image href="{static}logos/{logo}" x="{x + 6:g}" y="{y + 6:g}" width="{size - 12}" height="{size - 12}" preserveAspectRatio="xMidYMid meet"/>')


def icon_tile(x, y, name, size=40) -> str:
    return (f'<rect class="tile" x="{x:g}" y="{y:g}" width="{size}" height="{size}" rx="11"/>'
            f'<use class="icn" href="#i-{name}" x="{x + 8:g}" y="{y + 8:g}" width="{size - 16}" height="{size - 16}"/>')


def sprite() -> str:
    """The Lucide sprite every page includes once after <body>, minus its Jinja comment."""
    return re.sub(r"\{#-.*?-#\}", "", ICONS.read_text(), flags=re.S)


FIT_SCRIPT = """<script>
/* fit every box label to its box in this browser's font: start at full size, shrink only as far as it must */
(function () {
  function fit() {
    document.querySelectorAll('svg text[data-maxw]').forEach(function (t) {
      var max = +t.dataset.maxw, full = +t.dataset.fs || parseFloat(getComputedStyle(t).fontSize);
      t.style.fontSize = full + 'px';
      var len = t.getComputedTextLength();
      if (len > max) t.style.fontSize = (full * max / len * 0.98).toFixed(2) + 'px';
    });
  }
  fit();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);
})();
</script>"""


def fit_size(text: str, avail: float, size: float, per_char: float, floor: float) -> float:
    """A font size at which `text` fits in `avail` px (rough width = chars × per_char × size), never below `floor`.
    The server's guess, so the figure reads right without script; FIT_SCRIPT refines it in the browser."""
    need = len(text) * per_char * size
    return size if need <= avail else max(floor, size * avail / need)


def text(cls: str, s: str, x, y, avail: float, size: float, per_char: float = 0.5, floor: float = 9.0,
         anchor: str | None = None, extra: str = "", markup: str | None = None) -> str:
    """A <text> that FIT_SCRIPT can shrink to `avail` px; full size when it fits. `markup`
    (already escaped, e.g. coloured <tspan>s) replaces the escaped `s`, which still sets the width."""
    fs = fit_size(s, avail, size, per_char, floor)
    style = f' style="font-size:{fs:.1f}px"' if fs < size else ""
    an = f' text-anchor="{anchor}"' if anchor else ""
    return (f'<text class="{cls}" x="{x:g}" y="{y:g}"{an} data-maxw="{avail:.0f}" data-fs="{size:g}"{style}{extra}>'
            f'{esc(s) if markup is None else markup}</text>')


# The house figure CSS: the svg.pipe family of reports/2026-09_forecast_how_it_works.html
# (Roboto, 40-px tiles, rx 14) plus the phone list that replaces the figure at ≤ 700 px.
PIPE_CSS = """
.panel{background:#fff;border:1px solid #d9e4e8;border-radius:24px;padding:16px;overflow-x:auto}
svg.pipe{display:block;width:100%;min-width:760px;height:auto;font-family:Roboto,Arial,sans-serif}
.pipe-m{display:none;list-style:none;margin:0;padding:0}.pipe-m li{display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-top:1px solid #eef2f5}.pipe-m li:first-child{border-top:0}.pipe-m b{display:block;font-size:15px}.pipe-m span:last-child{font-size:13.5px;color:#54576F}.pipe-m .lgi img{width:26px;height:26px;object-fit:contain}
@media(max-width:700px){svg.pipe{display:none}.pipe-m{display:block}.panel{overflow:visible}}
.box{fill:#fff;stroke:#d9e4e8;stroke-width:1.5}.box.hub{stroke:#0072BC;stroke-width:2}.tile{fill:#E3EBF2}.tile.logo{fill:#fff;stroke:#e3ebf2;stroke-width:1.5}
.icn{fill:none;stroke:#0072BC;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.t{font-size:16px;font-weight:700;fill:#26272a}.s{font-size:13px;fill:#54576F}.col{font-size:11.5px;font-weight:700;letter-spacing:.12em;fill:#8a949b}
.flow{fill:none;stroke:#0072BC;stroke-width:2}.fit{fill:none;stroke:#8a949b;stroke-width:2;stroke-dasharray:6 5}.dot{fill:#0072BC}.lg{font-size:12px;fill:#54576F}
.lgi{display:inline-flex;width:44px;height:44px;border-radius:11px;background:#E3EBF2;align-items:center;justify-content:center;flex:0 0 auto}.lgi .ic{width:24px;height:24px;color:#0072BC}
.ic{width:1em;height:1em;vertical-align:-.15em;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
"""


def scoped(css: str, root: str = "svg.pipe") -> str:
    """Every selector of a figure's own CSS put under `root`, so its short class names can't
    restyle the page around it (brand.css and the reports use .lead, .pill and .chip for HTML)."""
    def one(sel: str) -> str:
        sel = sel.strip()
        return sel if sel.startswith(root) else f"{root} {sel}"
    return re.sub(r"([^{}]+)\{", lambda m: ",".join(one(s) for s in m.group(1).split(",")) + "{", css)


# ── geometry check: boxes are (x, y, w, h); points are (x, y) ────────────────

def rects_overlap(a, b) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def segments_cross(p1, p2, p3, p4) -> bool:
    """Proper crossing only: segments that meet at an end or run along each other are not a crossing."""
    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)
    return orient(p1, p2, p3) * orient(p1, p2, p4) < 0 and orient(p3, p4, p1) * orient(p3, p4, p2) < 0


def segment_hits_rect(a, b, r, shrink=0.5) -> bool:
    """Does the open segment a→b enter the rectangle's interior (shrunk by `shrink` so touching an edge is fine)?"""
    x, y, w, h = r[0] + shrink, r[1] + shrink, r[2] - 2 * shrink, r[3] - 2 * shrink
    for i in range(1, 200):
        t = i / 200
        px, py = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        if x < px < x + w and y < py < y + h:
            return True
    return False


def label_rect(s: str, x, y, anchor: str = "start", per_char: float = 6.0, height: float = 12):
    """A rough box around an 11-px label whose baseline is at y."""
    w = len(s) * per_char
    x0 = x - w if anchor == "end" else (x - w / 2 if anchor == "middle" else x)
    return (x0, y - height + 2, w, height)
