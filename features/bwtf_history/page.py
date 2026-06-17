"""BWTF Sample Log — the full Surfrider volunteer-lab record for SF.

A read-only page showing every BWTF sampling event with the rich per-sample
metadata the comparison page doesn't surface: who collected it (``testedBy``),
the field conditions (air/water temp, sky, wind, tide, waves, precipitation),
and the volunteer's comments. Data comes from the public BWTF GraphQL API via
``SFBWTFClient`` (features/comparison/bwtf_api.py); the page renders
``app/templates/bwtf_history/page.html`` and filters client-side.
"""
from datetime import datetime, timedelta

from flask import render_template

from features.comparison.bwtf_api import SFBWTFClient

# How much history to show by default (a year reads as a useful "log" without
# pulling the entire multi-year dataset into one HTML table).
DEFAULT_DAYS = 365
ENTERO_SSM = 104  # CA single-sample max — used only to flag exceedances


def _fmt_temp(value) -> str:
    return f"{value:.0f}°F" if value is not None else "—"


def _fmt_wind(direction: str, speed) -> str:
    parts = []
    if direction:
        parts.append(direction)
    if speed is not None:
        parts.append(f"{speed:.0f} mph")
    return " ".join(parts) or "—"


def _flatten(events: list[dict]) -> list[dict]:
    """One display row per analyte sample (SF events are usually one each)."""
    rows = []
    for e in events:
        when = e["collection_time"]
        w = e["weather"]
        for s in e["samples"]:
            val = s["result_value"]
            rows.append({
                "date": when.strftime("%Y-%m-%d") if when else "",
                "time": when.strftime("%-I:%M %p") if when else "",
                "site": e["site_name"],
                "substance": s["substance"],
                "result": s["result_display"],
                "method": s["method"] or "—",
                "exceeds": s["substance"] == "Enterococcus" and val is not None and val > ENTERO_SSM,
                "tested_by": e["tested_by"] or "—",
                "air": _fmt_temp(w["air_temp"]),
                "water": _fmt_temp(w["water_temp"]),
                "sky": w["current_weather"] or "—",
                "wind": _fmt_wind(w["wind_direction"], w["wind_speed"]),
                "tide": (w["tide"] or "—").title(),
                "waves": w["wave_height"] or "—",
                "rain": "Yes" if w["precipitation"] else ("No" if w["precipitation"] is False else "—"),
                "comments": e["comments"] or "",
            })
    return rows


def render_bwtf_history(days: int = DEFAULT_DAYS) -> str:
    client = SFBWTFClient()
    since = datetime.now() - timedelta(days=days)
    try:
        events = client.fetch_event_history(since=since)
    except Exception:
        events = []
    rows = _flatten(events)
    sites = sorted({r["site"] for r in rows if r["site"]})
    testers = sorted({r["tested_by"] for r in rows if r["tested_by"] and r["tested_by"] != "—"})
    return render_template(
        "bwtf_history/page.html",
        rows=rows,
        sites=sites,
        tester_count=len(testers),
        days=days,
        sample_count=len(rows),
        generated=datetime.now().strftime("%B %-d, %Y at %-I:%M %p"),
    )
