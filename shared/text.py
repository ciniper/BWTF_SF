"""Title case for the site's short text: labels, buttons, statuses, legends, map labels, table headers (2026-11-22).

Every word starts with a capital, except articles, coordinating conjunctions and prepositions (a, an, the, and, or,
for, of, to, in, on, at, by, with, from, ...) — those stay lowercase unless they are the first or last word, or follow
a colon, a dash or a "·" separator. Words that already have a capital, a digit or a unit (SFPUC, mL, E. coli, 3) are
left as they are. Full sentences stay in sentence case: do not pass them here.

The Today board's script has the same rule as `titleCase` (app/templates/_today_board.html)."""
import re

SMALL = frozenset("a an the and or nor but for of to in on at by as with from into per via vs vs. off onto over".split())
_SPLIT = re.compile(r"(\s+|[–—·/(),:|]+)")
_BREAK = re.compile(r"[:–—·|(]")


def title_case(text: str) -> str:
    toks = _SPLIT.split(text or "")
    words = [i for i, t in enumerate(toks) if re.search(r"[A-Za-z]", t or "")]
    out = list(toks)
    for n, i in enumerate(words):
        t = toks[i]
        if not re.fullmatch(r"[a-z][a-z'’\-]*", t):
            continue
        first = (n == 0 and not "".join(toks[:i]).strip()) or (n > 0 and bool(_BREAK.search("".join(toks[words[n - 1] + 1:i]))))   # "% of Days", "0.4 in of Rain" stay lowercase
        last = n == len(words) - 1 or bool(re.search(r"[:–—·|)]", "".join(toks[i + 1:words[n + 1]])))
        if t in SMALL and not first and not last:
            continue
        out[i] = "-".join(p[:1].upper() + p[1:] for p in t.split("-"))
    return "".join(out)
