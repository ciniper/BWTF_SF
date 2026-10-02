#!/usr/bin/env python3
"""Open-Meteo's archived rain forecasts at fixed leads, for S1 (rain forecast vs gauges).

The weather-model comparison of 2026-09-29 graded each model on Open-Meteo's
Historical Forecast archive. That archive is not "the forecast for tomorrow":
for every hour it keeps the newest run covering that hour, so it is a stitched
series of each run's first few hours. The page never sees rain that fresh
except for the next few hours, so a score on it is optimistic. The Previous
Runs API keeps the same model at fixed leads, which is what S1 must be graded
on (STAGES_DESIGN.md §3.1, §6 items 10–11).

Each model is read at one cell: Open-Meteo snaps 37.7749, −122.4194 to the
model's nearest land point — ICON 37.75, −122.50 (the Ocean Beach shore), GFS
37.76, −122.41 (downtown), ECMWF IFS 0.25° 37.75, −122.25 (Alameda, across
the bay: Open-Meteo prefers a land cell, and the 0.25° cell at −122.50 is
mostly sea). The served page reads the same cells. What each lead means, per
valid hour t:

  lead 0   ``precipitation`` — the newest run covering t, i.e. its first
           ≈0–6 hours (global runs every 6 h), stitched hour by hour. The same
           numbers as the Historical Forecast API (probed 2026-10-01: identical
           on every hour checked). SHORT LEAD, OPTIMISTIC: cached apart, in
           ``openmeteo_hist_forecast_<model>.csv``, and never written into a
           fixed-lead file.
  lead L   ``precipitation_previous_dayL`` (L = 1…5) — "the value predicted
           24·L hours before valid time" (Open-Meteo): the run that was current
           24·L h before t, so the lead time is 24·L h to 24·L h + one run
           interval. A daily total for day D at lead L therefore sums 24 hours
           each forecast during day D−L: the forecast for D as it stood on
           D−L, hour by hour. The page's own row for D on the morning of D−L
           uses one run for the whole day (leads 24·L − 7 h … 24·L + 17 h), so
           lead L is the closest archived stand-in for "L days ahead".

Day definition. Open-Meteo returns a "local" series at the UTC offset in force
when the request is made, for the whole span: the short-lead caches
(``openmeteo_hist_forecast_*.csv``) were fetched in September and are UTC−7
all year (checked 2026-10-01: all three match a GMT request shifted 7 h on
every hour, and the ICON cache does not match at 8 h). This module asks for GMT
and shifts by a fixed −7 h, so every file shares one 24-hour day whatever the
fetch date. In PST months (Nov–Mar) that day runs 23:00 → 22:59 PST, one hour
earlier than the page's own winter day; a caveat for midnight storms, the same
for every model and lead.

A day is complete only with all 24 hours archived (``daily_totals``). A NaN
hour is never a dry hour: ECMWF's Feb 2024 gap (306 hours, 14 days) used to
be summed as zero (X-S1-NWPGAP).

Files (features/forecast/data/raw):
  openmeteo_prev_runs_<model>.csv      timestamp, lead_day (1–5), precip_mm, precip_inches
                                       — fixed leads only; a lead's rows start at its first
                                       archived hour; a NaN hour is an empty cell
  openmeteo_hist_forecast_<model>.csv  timestamp, precip_mm, precip_inches — lead 0 (short
                                       lead, optimistic). ICON's extended back to 2022-11-16
                                       (first archived hour 2022-11-16 08:00 UTC, so the first
                                       complete day is 11-17), earlier rows prepended and the
                                       existing rows left byte-identical
  openmeteo_forecast_archive.json      manifest: what each file and lead is, first and last
                                       complete day per (model, lead), NaN hours, the probe

Be polite: 92-day chunks, a pause between requests, back off and retry on 429
and 5xx. Read-only public GETs; no key.

Usage (from the repo root):
  venv/bin/python features/forecast/src/collectors/openmeteo_previous_runs.py --probe
  venv/bin/python features/forecast/src/collectors/openmeteo_previous_runs.py --fetch [--end YYYY-MM-DD]
  venv/bin/python features/forecast/src/collectors/openmeteo_previous_runs.py --extend-icon
  venv/bin/python features/forecast/src/collectors/openmeteo_previous_runs.py --manifest
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from shared.clock import today_pacific, utc_iso  # noqa: E402

RAW_DIR = HERE.parents[1] / "data" / "raw"
MANIFEST = RAW_DIR / "openmeteo_forecast_archive.json"

LAT, LON = 37.7749, -122.4194                      # the point serving asks Open-Meteo for
MODELS = {"icon_seamless": "ICON", "ecmwf_ifs025": "ECMWF IFS 0.25°", "gfs_seamless": "GFS"}
FIXED_LEADS = (1, 2, 3, 4, 5)
LEADS = (0,) + FIXED_LEADS
LEAD_KIND = {0: "short_lead_optimistic", **{L: "fixed_lead" for L in FIXED_LEADS}}
PREV_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
HIST_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
UTC_OFFSET_HOURS = -7                              # see "Day definition" above
PREV_RUNS_FROM = pd.Timestamp("2024-01-01")        # "most models are archived from January 2024"
ICON_SHORT_LEAD_FROM = pd.Timestamp("2022-11-16")  # ICON's first archived hour: 2022-11-16 08:00 UTC
HOURS_PER_DAY = 24
CHUNK_DAYS = 92
PAUSE_S = 2.0
TRIES = 6
MM_PER_IN = 25.4


def prev_runs_path(model: str) -> Path:
    return RAW_DIR / f"openmeteo_prev_runs_{model}.csv"


def short_lead_path(model: str) -> Path:
    return RAW_DIR / f"openmeteo_hist_forecast_{model}.csv"


# ── fetching ────────────────────────────────────────────────────────────────

def _get(url: str, params: dict) -> dict:
    """GET with backoff on 429 / 5xx (15 s, 30 s, … capped at 5 min); anything else raises."""
    for i in range(TRIES):
        r = requests.get(url, params=params, timeout=120)
        if r.status_code == 429 or r.status_code >= 500:
            wait = min(300, 15 * 2 ** i)
            print(f"  {r.status_code} from {url.split('/')[2]}; retrying in {wait} s", flush=True)
            time.sleep(wait)
            continue
        r.raise_for_status()
        js = r.json()
        if js.get("error"):
            raise RuntimeError(f"Open-Meteo: {js.get('reason')}")
        return js
    raise RuntimeError(f"gave up after {TRIES} tries: {url} {params}")


def _fetch_span(url: str, model: str, variables: list[str], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Hourly ``variables`` (mm) for days start…end in the fixed UTC−7 day, fetched in GMT chunks.

    GMT dates start…end+1 cover the shifted span; rows outside it are cut.
    """
    frames, grid = [], None
    g0, g1 = start.normalize(), end.normalize() + pd.Timedelta(days=1)
    c0 = g0
    while c0 <= g1:
        c1 = min(c0 + pd.Timedelta(days=CHUNK_DAYS - 1), g1)
        js = _get(url, {"latitude": LAT, "longitude": LON, "hourly": ",".join(variables), "models": model,
                        "timezone": "GMT", "start_date": c0.strftime("%Y-%m-%d"), "end_date": c1.strftime("%Y-%m-%d")})
        h = js["hourly"]
        df = pd.DataFrame({"timestamp": pd.to_datetime(h["time"]) + pd.Timedelta(hours=UTC_OFFSET_HOURS)})
        for v in variables:
            df[v] = pd.to_numeric(pd.Series(h.get(v, [None] * len(df))), errors="coerce").to_numpy()
        frames.append(df)
        grid = [js.get("latitude"), js.get("longitude")]
        print(f"  {model} {c0.date()} → {c1.date()}: {len(df)} hours, grid {js.get('latitude')}, {js.get('longitude')}", flush=True)
        c0 = c1 + pd.Timedelta(days=1)
        if c0 <= g1:
            time.sleep(PAUSE_S)
    out = pd.concat(frames).drop_duplicates("timestamp").sort_values("timestamp")
    lo, hi = start.normalize(), end.normalize() + pd.Timedelta(hours=23)
    out = out[(out["timestamp"] >= lo) & (out["timestamp"] <= hi)].reset_index(drop=True)
    out.attrs["grid"] = grid                         # the model cell Open-Meteo actually read
    return out


def latest_complete_day() -> pd.Timestamp:
    """Yesterday on the beach's calendar: the newest day whose 24 hours (UTC−7) are all in the past."""
    return pd.Timestamp(today_pacific()) - pd.Timedelta(days=1)


def fetch_previous_runs(model: str, start: pd.Timestamp = PREV_RUNS_FROM, end: pd.Timestamp | None = None,
                        write: bool = True) -> tuple[pd.DataFrame, dict]:
    """Leads 1–5 for ``model`` from ``start`` to ``end`` (default yesterday) → the long cache.

    Each lead keeps rows from its first archived hour on (earlier hours are
    "not in the archive", X-S1-NOLEAD, not NaN). Lead 0 comes in the same
    request and is only checked against the short-lead cache, never stored here.
    """
    end = latest_complete_day() if end is None else pd.Timestamp(end)
    variables = ["precipitation"] + [f"precipitation_previous_day{L}" for L in FIXED_LEADS]
    wide = _fetch_span(PREV_RUNS_URL, model, variables, pd.Timestamp(start), end)
    rows = []
    for L in FIXED_LEADS:
        s = wide[["timestamp", f"precipitation_previous_day{L}"]].rename(columns={f"precipitation_previous_day{L}": "precip_mm"})
        first = s["precip_mm"].first_valid_index()
        if first is None:
            continue
        s = s.loc[first:].copy()
        s.insert(1, "lead_day", L)
        rows.append(s)
    long = pd.concat(rows, ignore_index=True)
    long["precip_inches"] = long["precip_mm"] / MM_PER_IN
    check = {"grid": wide.attrs.get("grid")}
    sl = short_lead_path(model)
    if sl.exists():                                   # lead 0 here should be the short-lead archive itself
        h = pd.read_csv(sl, parse_dates=["timestamp"]).set_index("timestamp")["precip_mm"]
        j = pd.concat([wide.set_index("timestamp")["precipitation"].rename("pr"), h.rename("hf")], axis=1, join="inner").dropna()
        check.update(hours_compared=int(len(j)), identical=int((np.abs(j["pr"] - j["hf"]) < 1e-9).sum()))
    if write:
        long.to_csv(prev_runs_path(model), index=False)
    return long, check


def extend_short_lead(model: str = "icon_seamless", start: pd.Timestamp = ICON_SHORT_LEAD_FROM) -> dict:
    """Prepend the Historical Forecast archive from ``start`` to the short-lead cache.

    The cache's existing rows stay byte-identical (the new rows are written as
    text ahead of them), and one overlapping day is fetched and must match the
    cache exactly, so the UTC−7 convention is proved, not assumed.
    """
    path = short_lead_path(model)
    text = path.read_text()
    header, body = text.split("\n", 1)
    have = pd.read_csv(path, parse_dates=["timestamp"])
    first = have["timestamp"].min()
    if first <= pd.Timestamp(start):
        return {"model": model, "prepended_hours": 0, "first": str(first)}
    new = _fetch_span(HIST_FORECAST_URL, model, ["precipitation"], pd.Timestamp(start), first + pd.Timedelta(days=1))
    new = new.rename(columns={"precipitation": "precip_mm"})
    overlap = new.merge(have[["timestamp", "precip_mm"]], on="timestamp", suffixes=("", "_have"))
    same = ((overlap["precip_mm"] - overlap["precip_mm_have"]).abs() < 1e-9) | (overlap["precip_mm"].isna() & overlap["precip_mm_have"].isna())
    if len(overlap) < HOURS_PER_DAY or not same.all():
        raise RuntimeError(f"{model}: the fetched archive does not match the cache on the overlap ({int(same.sum())}/{len(overlap)})")
    new = new[new["timestamp"] < first].copy()
    new["precip_inches"] = new["precip_mm"] / MM_PER_IN
    if header.split(",") != ["timestamp", "precip_mm", "precip_inches"]:
        raise RuntimeError(f"unexpected header in {path.name}: {header}")
    block = new[["timestamp", "precip_mm", "precip_inches"]].to_csv(index=False, header=False, date_format="%Y-%m-%d %H:%M:%S")
    path.write_text(header + "\n" + block + body)
    return {"model": model, "prepended_hours": int(len(new)), "first": str(new["timestamp"].min()),
            "overlap_hours_identical": int(same.sum())}


# ── reading (the one place the complete-day rule lives) ─────────────────────

def load_hourly(model: str, lead: int) -> pd.DataFrame:
    """Hourly (timestamp, precip_mm, precip_inches) for one model and lead; lead 0 = the short-lead cache."""
    if lead == 0:
        return pd.read_csv(short_lead_path(model), parse_dates=["timestamp"])
    df = pd.read_csv(prev_runs_path(model), parse_dates=["timestamp"])
    return df[df["lead_day"] == lead].drop(columns="lead_day").reset_index(drop=True)


def daily_totals(hourly: pd.DataFrame, col: str = "precip_inches") -> pd.DataFrame:
    """date → (total, hours): the day's sum and its count of archived (non-NaN) hours.

    ``total`` is NaN unless all 24 hours are archived: a missing hour is never
    a dry hour (X-S1-NWPGAP). Days the archive does not reach are absent, not
    NaN rows (X-S1-NOLEAD is decided from ``archive_span``).
    """
    h = hourly[["timestamp", col]].copy()
    h["date"] = pd.to_datetime(h["timestamp"]).dt.normalize()
    g = h.groupby("date")[col]
    out = pd.DataFrame({"total": g.sum(min_count=1), "hours": g.count()})
    out.loc[out["hours"] < HOURS_PER_DAY, "total"] = np.nan
    return out


def archive_span(hourly: pd.DataFrame, col: str = "precip_inches") -> tuple:
    """(first, last) day holding any archived hour; days outside are "not in the archive"."""
    ok = hourly.loc[hourly[col].notna(), "timestamp"]
    if ok.empty:
        return None, None
    return pd.Timestamp(ok.min()).normalize(), pd.Timestamp(ok.max()).normalize()


def daily_by_lead(model: str, leads=LEADS) -> pd.DataFrame:
    """date × lead → daily total (inches), NaN where incomplete or outside the archive."""
    cols = {}
    for L in leads:
        try:
            cols[L] = daily_totals(load_hourly(model, L))["total"]
        except FileNotFoundError:
            continue
    return pd.DataFrame(cols).sort_index()


# ── probe and manifest ──────────────────────────────────────────────────────

def probe(start: str = "2024-01-10", end: str = "2024-02-20") -> dict:
    """First archived hour and first complete day per (model, lead), and lead 0 vs the short-lead cache."""
    out = {"window": [start, end], "models": {}}
    variables = ["precipitation"] + [f"precipitation_previous_day{L}" for L in FIXED_LEADS]
    for i, m in enumerate(MODELS):
        if i:
            time.sleep(PAUSE_S)
        w = _fetch_span(PREV_RUNS_URL, m, variables, pd.Timestamp(start), pd.Timestamp(end))
        rec = {"grid": w.attrs.get("grid")}
        for L, v in zip(LEADS, variables):
            d = daily_totals(w.rename(columns={v: "x"})[["timestamp", "x"]], col="x")
            ok = w.loc[w[v].notna(), "timestamp"]
            rec[str(L)] = {"first_hour": str(ok.min()) if len(ok) else None,
                           "first_complete_day": str(d.index[d["total"].notna()].min().date()) if d["total"].notna().any() else None}
        sl = short_lead_path(m)
        if sl.exists():
            h = pd.read_csv(sl, parse_dates=["timestamp"]).set_index("timestamp")["precip_mm"]
            j = pd.concat([w.set_index("timestamp")["precipitation"].rename("pr"), h.rename("hf")], axis=1, join="inner").dropna()
            rec["lead0_vs_short_lead_cache"] = {"hours": int(len(j)), "identical": int((np.abs(j["pr"] - j["hf"]) < 1e-9).sum())}
        out["models"][m] = rec
    time.sleep(PAUSE_S)
    w = _fetch_span(HIST_FORECAST_URL, "icon_seamless", ["precipitation"], ICON_SHORT_LEAD_FROM - pd.Timedelta(days=2),
                    ICON_SHORT_LEAD_FROM + pd.Timedelta(days=3))
    ok = w.loc[w["precipitation"].notna(), "timestamp"]
    out["icon_short_lead_first_hour"] = str(ok.min()) if len(ok) else None
    return out


def summarize(model: str, lead: int) -> dict | None:
    try:
        h = load_hourly(model, lead)
    except FileNotFoundError:
        return None
    d = daily_totals(h)
    lo, hi = archive_span(h)
    comp = d.index[d["total"].notna()]
    inside = d.loc[lo:hi] if lo is not None else d.iloc[:0]
    return {"kind": LEAD_KIND[lead], "file": (short_lead_path if lead == 0 else prev_runs_path)(model).name,
            "first_archived_day": str(lo.date()) if lo is not None else None, "last_archived_day": str(hi.date()) if hi is not None else None,
            "first_complete_day": str(comp.min().date()) if len(comp) else None, "last_complete_day": str(comp.max().date()) if len(comp) else None,
            "complete_days": int(len(comp)), "incomplete_days_inside_span": int(inside["total"].isna().sum()),
            "nan_hours_inside_span": int(h.loc[(h["timestamp"] >= lo) & (h["timestamp"] < hi + pd.Timedelta(days=1)), "precip_inches"].isna().sum()) if lo is not None else 0}


def write_manifest(probe_result: dict | None = None, notes: dict | None = None) -> dict:
    old = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    man = {
        "schema": "bwtf.openmeteo_forecast_archive/1", "written_at": utc_iso(),
        "point": {"latitude": LAT, "longitude": LON}, "utc_offset_hours": UTC_OFFSET_HOURS,
        "day": "fixed UTC−7, 24 hours (Open-Meteo's request-time offset when the caches were first fetched)",
        "complete_day_hours": HOURS_PER_DAY,
        "leads": {"0": "short lead: the newest run covering each hour, stitched (≈0–6 h); optimistic; the Historical Forecast archive",
                  **{str(L): f"fixed lead: the value predicted {24 * L} h before valid time (precipitation_previous_day{L})" for L in FIXED_LEADS}},
        "urls": {"fixed_lead": PREV_RUNS_URL, "short_lead": HIST_FORECAST_URL},
        "models": {m: {str(L): summarize(m, L) for L in LEADS} for m in MODELS},
        "probe": probe_result if probe_result is not None else old.get("probe"),
        "fetch_notes": {**(old.get("fetch_notes") or {}), **(notes or {})},
    }
    MANIFEST.write_text(json.dumps(man, indent=1, ensure_ascii=False) + "\n")
    return man


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--probe", action="store_true", help="find each (model, lead)'s first archived day")
    ap.add_argument("--fetch", action="store_true", help="fetch leads 1–5 for every model")
    ap.add_argument("--extend-icon", action="store_true", help="prepend ICON's short lead back to 2022-11-16")
    ap.add_argument("--manifest", action="store_true", help="rewrite the manifest from the caches")
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--end", default=None, help="last day to fetch (default: yesterday, Pacific)")
    a = ap.parse_args(argv)
    pr, notes = None, {}
    if a.probe:
        pr = probe()
        print(json.dumps(pr, indent=1))
    if a.extend_icon:
        notes["icon_short_lead_extension"] = {**extend_short_lead("icon_seamless"), "fetched_at": utc_iso()}
        print(notes["icon_short_lead_extension"])
    if a.fetch:
        for i, m in enumerate(a.models.split(",")):
            if i:
                time.sleep(PAUSE_S)
            long, check = fetch_previous_runs(m, end=a.end)
            notes[f"prev_runs_{m}"] = {"fetched_at": utc_iso(), "rows": int(len(long)), "lead0_vs_short_lead_cache": check}
            print(f"{m}: {len(long)} rows → {prev_runs_path(m).name}; lead 0 vs the short-lead cache {check}")
    if a.probe or a.fetch or a.extend_icon or a.manifest:
        man = write_manifest(pr, notes)
        for m, leads in man["models"].items():
            print(m, {L: (s or {}).get("first_complete_day") for L, s in leads.items()})


if __name__ == "__main__":
    main()
