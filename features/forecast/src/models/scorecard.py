"""One rule for the model-check scorecard (2026-09).

The training-time hindcast (``scorecard.json.gz`` → ``days``) records, per
day, each zone's composed risk from the final model (``risk``) and from the
holdout-fit model (``risk_h``, only from HOLDOUT_START on), next to the labels
(``discharge`` = a reported discharge posted one of the zone's beaches,
``elevated`` = a sample over standard). This module turns any date window of
those days into the scorecard numbers. It is shared by:

- ``train_v4.build_scorecard`` — the season block stored in the artifact
  (whole holdout, holdout-fit model only), and
- ``live_dashboard.get_scorecard`` — the time-boxed block the Model check page
  requests for a season, the holdout, everything, or a custom range.

Keeping the two on one function is what lets the page's numbers be checked
against the artifact (``tests/test_scorecard_window.py``).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

THRESHOLDS = (0.10, 0.25, 0.50)   # the lines the trainer stores in the artifact's season block
# the finer grid the served window is scored on, so "cheapest line" for a
# cost ratio (false alarms + N × misses) has somewhere to land; includes THRESHOLDS
LINE_GRID = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60, 0.75)
BASIN_KEYS = ("westside", "north_shore", "central", "southeast")


# ── seasons ────────────────────────────────────────────────────────────────
# A rain season runs 1 July → 30 June and is named for the July year, matching
# the ``season`` column the trainer writes on every hindcast day.

def season_of(day: str | date) -> int:
    d = datetime.strptime(day, "%Y-%m-%d").date() if isinstance(day, str) else day
    return d.year if d.month >= 7 else d.year - 1


def season_bounds(season: int) -> tuple[str, str]:
    return f"{season}-07-01", f"{season + 1}-06-30"


def season_label(season: int) -> str:
    return f"{season}–{str(season + 1)[2:]}"


def _mon(day: str) -> str:
    return datetime.strptime(day, "%Y-%m-%d").strftime("%b %Y")


def _next_day(day: str) -> str:
    return str(datetime.strptime(day, "%Y-%m-%d").date() + timedelta(days=1))


def _overlap(lo: str, hi: str, a: str | None, b: str | None) -> str:
    """How much of [lo, hi] lies inside [a, b]: 'none' | 'partial' | 'full'."""
    if a is None or b is None or hi < a or lo > b:
        return "none"
    return "full" if lo >= a and hi <= b else "partial"


def seasons_in(span: tuple[str, str] | list, holdout_start: str | None = None,
               trained_through: str | None = None) -> list[dict]:
    """Every season touching ``span`` (newest first) with its clamped bounds and
    what kind of probabilities it holds: ``holdout`` = share of the season the
    holdout-fit model covers (holdout_start → trained_through), ``post`` =
    share after training (served models on days they never saw), each
    'none' | 'partial' | 'full'; the rest is in-sample. ``clip`` names the cut
    when the artifact does not hold the whole season."""
    first, last = span
    trained_through = trained_through or last
    post_start = _next_day(trained_through) if trained_through < last else None
    out = []
    for s in range(season_of(last), season_of(first) - 1, -1):
        slo, shi = season_bounds(s)
        lo, hi = max(slo, first), min(shi, last)
        out.append({"season": s, "label": season_label(s), "start": lo, "end": hi,
                    "holdout": _overlap(lo, hi, holdout_start, trained_through),
                    "post": _overlap(lo, hi, post_start, last),
                    "clip": f"from {_mon(lo)}" if lo > slo else f"through {_mon(hi)}" if hi < shi else None})
    return out


def grade(n_holdout: int, n_post: int, n_insample: int) -> str:
    """One word for what a window's probabilities are."""
    if not (n_holdout or n_post or n_insample):
        return "empty"
    if n_insample:
        return "in_sample" if not (n_holdout or n_post) else "mixed"
    if n_holdout and n_post:
        return "out_of_sample"
    return "holdout" if n_holdout else "post_training"


def day_kind(day: dict) -> str:
    """'holdout' (has a holdout-fit probability) | 'post' (after training) | 'insample'."""
    if day.get("post_training"):
        return "post"
    z = next(iter(day["zones"].values()), {}) if day.get("zones") else {}
    return "holdout" if z.get("risk_h") is not None else "insample"


# ── windows ────────────────────────────────────────────────────────────────

def clamp_window(start: str | None, end: str | None, span: tuple[str, str] | list) -> tuple[str, str]:
    """Clamp a requested window to the artifact span; swap if reversed."""
    first, last = span
    lo = start or first
    hi = end or last
    if lo > hi:
        lo, hi = hi, lo
    return max(lo, first), min(hi, last)


def window_days(days: list[dict], start: str | None = None, end: str | None = None) -> list[dict]:
    return [d for d in days if (start is None or d["date"] >= start) and (end is None or d["date"] <= end)]


def _prob(z: dict, holdout_only: bool):
    """The probability to score a zone/basin day with: holdout-fit where the
    holdout model saw the day, else (unless holdout_only) the final model's
    in-sample probability. None → skip the day."""
    ph = z.get("risk_h", z.get("ph"))
    if ph is not None:
        return ph
    if holdout_only:
        return None
    return z.get("risk", z.get("p"))


# ── zone confusion ─────────────────────────────────────────────────────────

def zone_confusion(days: list[dict], zone_keys, start: str | None = None, end: str | None = None,
                   holdout_only: bool = True, thresholds=THRESHOLDS) -> dict:
    """{zone: {"0.25": {"vs_discharge_posting": {tp,fp,fn,tn},
                        "vs_bacteria_elevated": {tp,fp,fn,tn}}, ...}}

    A day counts toward the discharge confusion only when its discharge label
    is known (covered by CIWQS or the feed archive), and toward the bacteria
    confusion only when the zone was sampled. ``holdout_only=True`` reproduces
    the artifact's season block exactly."""
    out = {}
    subset = window_days(days, start, end)
    for zk in zone_keys:
        out[zk] = {}
        for thr in thresholds:
            c_d = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
            c_b = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
            for day in subset:
                z = day["zones"][zk]
                p = _prob(z, holdout_only)
                if p is None:
                    continue
                pred = p >= thr
                if z["discharge"] is not None:
                    c_d["tp" if pred and z["discharge"] else "fp" if pred else "fn" if z["discharge"] else "tn"] += 1
                if z["elevated"] is not None:
                    c_b["tp" if pred and z["elevated"] else "fp" if pred else "fn" if z["elevated"] else "tn"] += 1
            out[zk][str(thr)] = {"vs_discharge_posting": c_d, "vs_bacteria_elevated": c_b}
    return out


TAIL_DAYS = 7   # the composition holds risk up for a week after a discharge (impact.compose k = 1..7)


def zone_fp_tail(days: list[dict], zone_keys, start: str | None = None, end: str | None = None,
                 holdout_only: bool = False, thresholds=THRESHOLDS) -> dict:
    """{zone: {"0.25": n, ...}} — how many of the window's false alarms (vs the
    discharge label) fall within TAIL_DAYS after a day a discharge posted the
    zone. Those are the composition doing what it is designed to do (the beach
    is likely still dirty); the remainder are alarms on clean days. Postings
    just before the window count, so a window's first days are judged fairly.
    Kept apart from zone_confusion so the artifact's stored block stays as is."""
    out = {}
    subset = window_days(days, start, end)
    for zk in zone_keys:
        posted = sorted(d["date"] for d in days if zk in d["zones"] and d["zones"][zk]["discharge"])
        posted_set = set(posted)

        def in_tail(ds: str) -> bool:
            d0 = datetime.strptime(ds, "%Y-%m-%d").date()
            return any(str(d0 - timedelta(days=k)) in posted_set for k in range(1, TAIL_DAYS + 1))

        out[zk] = {}
        for thr in thresholds:
            n = 0
            for day in subset:
                z = day["zones"][zk]
                p = _prob(z, holdout_only)
                if p is None or z["discharge"] is None or z["discharge"] or p < thr:
                    continue
                if in_tail(day["date"]):
                    n += 1
            out[zk][str(thr)] = n
    return out


# ── zone confusion against the posting label ──────────────────────────────

POSTED_IN_SCOPE = ("cso", "rain")   # see posting_label.IN_SCOPE


def zone_confusion_posted(days: list[dict], zone_keys, label, start: str | None = None, end: str | None = None,
                          holdout_only: bool = False, thresholds=THRESHOLDS) -> dict:
    """{zone: {"0.25": {tp, fp, fn, tn, fp_after_discharge, other_posted,
    other_flagged, unknown}, ...}} against the *posting* label (the signs on
    the beach — ``posting_label.PostingLabel``), not the discharge day.

    A posted day whose cause rain can explain (``cso`` / ``rain``) is a hit
    when the zone's risk reaches the line and a miss otherwise. A day the
    record covers with no posting is quiet: an alarm there is the false alarm,
    and ``fp_after_discharge`` counts those falling within TAIL_DAYS of a
    discharge (the beach already reopened, the model still up). Postings for
    other causes (dry-weather bacteria, unexplained) are out of a rain model's
    scope and are only counted (``other_posted`` / ``other_flagged``). Days
    the record does not cover are ``unknown`` and not graded."""
    out = {}
    subset = window_days(days, start, end)
    for zk in zone_keys:
        dis_set = {d["date"] for d in days if zk in d["zones"] and d["zones"][zk]["discharge"]}

        def in_tail(ds: str) -> bool:
            d0 = datetime.strptime(ds, "%Y-%m-%d").date()
            return any(str(d0 - timedelta(days=k)) in dis_set for k in range(1, TAIL_DAYS + 1))

        out[zk] = {}
        for thr in thresholds:
            c = {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "fp_after_discharge": 0, "other_posted": 0, "other_flagged": 0, "unknown": 0}
            for day in subset:
                z = day["zones"][zk]
                p = _prob(z, holdout_only)
                if p is None:
                    continue
                cls = label.cls(zk, day["date"])
                if cls == "unknown":
                    c["unknown"] += 1
                    continue
                pred = p >= thr
                if cls in POSTED_IN_SCOPE:
                    c["tp" if pred else "fn"] += 1
                elif cls is not None:
                    c["other_posted"] += 1
                    c["other_flagged"] += int(pred)
                else:
                    c["fp" if pred else "tn"] += 1
                    if pred and in_tail(day["date"]):
                        c["fp_after_discharge"] += 1
            out[zk][str(thr)] = c
    return out


# ── stage 1 per basin ──────────────────────────────────────────────────────

def basin_metrics(days: list[dict], basin_keys=BASIN_KEYS, start: str | None = None, end: str | None = None,
                  holdout_only: bool = False) -> dict:
    """Per basin over the window: labelled days, discharge days, and — when
    both classes are present — PR-AUC, ROC-AUC and Brier of the scored
    probability (holdout-fit where available, else in-sample)."""
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

    out = {}
    subset = window_days(days, start, end)
    for key in basin_keys:
        y, p, n_hold, n_post = [], [], 0, 0
        for day in subset:
            b = (day.get("basins") or {}).get(key)
            if not b or b.get("y") is None:
                continue
            prob = _prob(b, holdout_only)
            if prob is None:
                continue
            y.append(int(b["y"]))
            p.append(float(prob))
            n_hold += b.get("ph") is not None
            n_post += bool(day.get("post_training"))
        row = {"n_days": len(y), "n_events": int(sum(y)), "n_holdout": n_hold, "n_post": n_post,
               "pr_auc": None, "roc_auc": None, "brier": None}
        if y and 0 < sum(y) < len(y):
            row["pr_auc"] = float(average_precision_score(y, p))
            row["roc_auc"] = float(roc_auc_score(y, p))
        if y:
            row["brier"] = float(brier_score_loss(y, p))
        out[key] = row
    return out


# ── window summary ─────────────────────────────────────────────────────────

def window_summary(days: list[dict], zone_keys, start: str | None = None, end: str | None = None) -> dict:
    subset = window_days(days, start, end)
    kinds = [day_kind(d) for d in subset]
    n_hold, n_post = kinds.count("holdout"), kinds.count("post")
    n_in = len(subset) - n_hold - n_post
    dis = sum(1 for d in subset if any(d["zones"][zk]["discharge"] for zk in zone_keys if zk in d["zones"]))
    known = sum(1 for d in subset if any(d["zones"][zk]["discharge"] is not None for zk in zone_keys if zk in d["zones"]))
    sampled = sum(1 for d in subset if any(d["zones"][zk]["elevated"] is not None for zk in zone_keys if zk in d["zones"]))
    return {"n_days": len(subset), "n_holdout": n_hold, "n_post": n_post, "n_insample": n_in,
            "grade": grade(n_hold, n_post, n_in),
            "n_discharge_known": known, "n_discharge_days": dis, "n_sampled_days": sampled}
