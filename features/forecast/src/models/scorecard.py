"""One rule for the model-check scorecard (v4, 2026-09).

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

from datetime import date, datetime

THRESHOLDS = (0.10, 0.25, 0.50)
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


def seasons_in(span: tuple[str, str] | list, holdout_start: str | None = None) -> list[dict]:
    """Every season touching ``span`` (newest first) with its clamped bounds
    and whether the holdout-fit model covers it fully, partly, or not at all."""
    first, last = span
    out = []
    for s in range(season_of(last), season_of(first) - 1, -1):
        lo, hi = season_bounds(s)
        lo, hi = max(lo, first), min(hi, last)
        if holdout_start is None or hi < holdout_start:
            hold = "none"
        elif lo >= holdout_start:
            hold = "full"
        else:
            hold = "partial"
        out.append({"season": s, "label": season_label(s), "start": lo, "end": hi, "holdout": hold})
    return out


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
        y, p, n_hold = [], [], 0
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
        row = {"n_days": len(y), "n_events": int(sum(y)), "n_holdout": n_hold,
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
    n_hold = sum(1 for d in subset if d["zones"] and next(iter(d["zones"].values())).get("risk_h") is not None)
    dis = sum(1 for d in subset if any(d["zones"][zk]["discharge"] for zk in zone_keys if zk in d["zones"]))
    known = sum(1 for d in subset if any(d["zones"][zk]["discharge"] is not None for zk in zone_keys if zk in d["zones"]))
    sampled = sum(1 for d in subset if any(d["zones"][zk]["elevated"] is not None for zk in zone_keys if zk in d["zones"]))
    return {"n_days": len(subset), "n_holdout": n_hold, "n_insample": len(subset) - n_hold,
            "n_discharge_known": known, "n_discharge_days": dis, "n_sampled_days": sampled}
