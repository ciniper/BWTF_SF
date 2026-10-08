#!/usr/bin/env python3
"""Gridded rain over San Francisco, cut to the city and averaged per region (Chase, 2026-10-08: "do a big
investigation with your recs on a branch and give a report"; features/forecast/RAIN_SOURCES.md). Research inputs:
nothing served reads them.

    source  what                                                      record                 step
    aorc    NOAA's Analysis of Record for Calibration v1.1, ~800 m    1979 → Dec 2025 (AWS)  hourly
    mrms    NOAA MRMS MultiSensor QPE Pass2 (radar + gauges), 1 km    Oct 2020 →             hourly; 24 h at midnight
    aqpi    the Bay Area AQPI radar network, Surfrider's feed, 250 m  Oct 2025 → Aug 2026    15 minutes

Regions (``REGIONS``): the two NOAA gauges' own points (the grid cell nearest each), and five rough rectangles, the
four BWTF basins and the whole city. They are NOT sewershed outlines: the repo has none (AQPI's ``name`` column is a
federal HUC12 watershed, not a sewer basin). The rectangles follow the facilities' split (the Oceanside plant takes
the west side, Bayside the rest) and cut the east side by latitude into North Shore, Central and Southeast. SFPUC's
drainage-basin outlines would replace them.

Output, in the repo (small): ``data/raw/rain_grids/<source>_hourly.csv.gz``, one row per UTC hour (stamped at the
hour's end), one column per region, inches; MRMS adds ``mrms_daily.csv.gz`` from its 24-hour product at Pacific
midnight (every day of its record; its hourly file holds the wet days only, ``wet_days``). Raw cut-outs of the city
box, outside the repo: ``~/Personal/BWTF_Data_Archive/07_rain_grids/<source>/``.

    <rain env>/bin/python features/forecast/src/collectors/rain_grids.py aorc [--years 2010-2025]
    <rain env>/bin/python features/forecast/src/collectors/rain_grids.py aqpi
    <rain env>/bin/python features/forecast/src/collectors/rain_grids.py mrms-daily
    <rain env>/bin/python features/forecast/src/collectors/rain_grids.py mrms-hourly

Needs the packages in ``features/forecast/requirements-rain.txt`` (xarray, zarr, s3fs, eccodes), kept out of the
app's requirements.txt and its Vercel bundle. AWS reads are anonymous (NOAA Open Data Dissemination).
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
OUT_DIR = FORECAST / "data" / "raw" / "rain_grids"
ARCHIVE = Path.home() / "Personal" / "BWTF_Data_Archive" / "07_rain_grids"
AQPI_DIR = Path.home() / "Personal" / "BWTF_Data_Archive" / "06_aqpi_radar_rain" / "qpe15min_daily"
MM_PER_IN = 25.4
TZ = "America/Los_Angeles"

# the city box every source is cut to before anything else (a margin around REGIONS)
BOX = {"lon": (-122.53, -122.34), "lat": (37.69, 37.83)}
# the NOAA gauges (ACIS StnMeta, 2026-10-08): SF Downtown USW00023272 / 047772, SF Oceanside USC00047767 / 047767
POINTS = {"downtown_gauge": (-122.4269, 37.7705), "oceanside_gauge": (-122.5052, 37.7280)}
# rough rectangles, (lon_min, lon_max, lat_min, lat_max), cell centres inside: see the module notes
RECTS = {"westside": (-122.512, -122.455, 37.708, 37.790),
         "north_shore": (-122.455, -122.385, 37.785, 37.806),
         "central": (-122.455, -122.385, 37.745, 37.785),
         "southeast": (-122.455, -122.380, 37.708, 37.745),
         "city": (-122.512, -122.380, 37.708, 37.806)}
REGIONS = list(POINTS) + list(RECTS)

AORC_BUCKET = "noaa-nws-aorc-v1-1-1km"
MRMS_BUCKET = "noaa-mrms-pds/CONUS"
MRMS_01H = "MultiSensor_QPE_01H_Pass2_00.00"
MRMS_24H = "MultiSensor_QPE_24H_Pass2_00.00"
MRMS_START = pd.Timestamp("2020-10-15")


def region_weights(lon: np.ndarray, lat: np.ndarray) -> dict:
    """{region: boolean mask over the cells}: a point's nearest cell, a rectangle's cells whose centres it holds."""
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    out = {}
    for name, (x, y) in POINTS.items():
        d = ((lon - x) * np.cos(np.radians(y))) ** 2 + (lat - y) ** 2
        m = np.zeros(lon.shape, bool)
        m[np.unravel_index(np.nanargmin(d), lon.shape)] = True
        out[name] = m
    for name, (x0, x1, y0, y1) in RECTS.items():
        m = (lon >= x0) & (lon < x1) & (lat >= y0) & (lat < y1)
        if not m.any():
            raise ValueError(f"region {name} holds no cell")
        out[name] = m
    return out


def region_means(values: np.ndarray, masks: dict) -> dict:
    """values: (time, *cells) → {region: (time,) mean over its cells, NaN cells skipped, all-NaN → NaN}."""
    flat = values.reshape(values.shape[0], -1)
    out = {}
    for name, m in masks.items():
        sel = flat[:, m.reshape(-1)]
        with np.errstate(invalid="ignore"):
            n = np.sum(~np.isnan(sel), axis=1)
            s = np.nansum(sel, axis=1)
        out[name] = np.where(n > 0, s / np.maximum(n, 1), np.nan)
    return out


def write(source: str, frame: pd.DataFrame, kind: str = "hourly") -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / f"{source}_{kind}.csv.gz"
    frame = frame.sort_index()
    text = frame.round(4).to_csv(index_label="timestamp_utc" if kind == "hourly" else "date", float_format="%.4f")
    with open(p, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as f:   # no clock: a rerun writes the same bytes
        f.write(text.encode())
    print(f"wrote {p.relative_to(FORECAST.parents[1])} ({len(frame):,} rows, {p.stat().st_size / 1e6:.2f} MB)")
    return p


def read(source: str, kind: str = "hourly") -> pd.DataFrame:
    p = OUT_DIR / f"{source}_{kind}.csv.gz"
    col = "timestamp_utc" if kind == "hourly" else "date"
    f = pd.read_csv(p, parse_dates=[col]).set_index(col)
    if kind == "hourly":
        f.index = pd.to_datetime(f.index, utc=True)
    return f


# ── AORC ─────────────────────────────────────────────────────────────────────

def aorc(years: list) -> Path:
    """The city box of every AORC year, hourly mm → region means in inches. AORC's stamp (wgrib2's verification time)
    is the hour's end: against MRMS's hourly product, stamped at its accumulation's end, over 10,689 wet hours of
    Oct 2020 – Dec 2025, the correlation peaks with no shift (rain_sources.clock; RAIN_SOURCES.md)."""
    import s3fs
    import xarray as xr
    fs = s3fs.S3FileSystem(anon=True)
    (ARCHIVE / "aorc").mkdir(parents=True, exist_ok=True)
    parts, masks = [], None
    for y in years:
        t0 = time.time()
        npz = ARCHIVE / "aorc" / f"aorc_sf_{y}.npz"
        if not npz.exists():                                                 # the cut-out once; reruns read it
            ds = xr.open_zarr(s3fs.S3Map(f"{AORC_BUCKET}/{y}.zarr", s3=fs), consolidated=True)
            box = ds["APCP_surface"].sel(latitude=slice(*BOX["lat"]), longitude=slice(*BOX["lon"])).load()
            np.savez_compressed(npz, apcp_mm=box.values.astype("float32"), time=box.time.values.astype("datetime64[s]"),
                                lat=box.latitude.values, lon=box.longitude.values)
        z = np.load(npz)
        v = z["apcp_mm"]                                                     # (time, lat, lon), mm in the hour
        if masks is None:
            lon, lat = np.meshgrid(z["lon"], z["lat"])
            masks = region_weights(lon, lat)
        means = region_means(v.astype(float), masks)
        idx = pd.DatetimeIndex(z["time"]).tz_localize("UTC") + pd.Timedelta(hours=AORC_SHIFT_H)
        parts.append(pd.DataFrame({k: means[k] / MM_PER_IN for k in REGIONS}, index=idx))
        print(f"  aorc {y}: {v.shape}, max {np.nanmax(v):.1f} mm/h, {time.time() - t0:.0f}s")
    return write("aorc", pd.concat(parts))


AORC_SHIFT_H = 0      # hours added to an AORC stamp to give the hour's end: none, its stamp IS the hour's end (aorc())


# ── AQPI ─────────────────────────────────────────────────────────────────────

def _aqpi_file(path: str) -> pd.DataFrame | None:
    f = pd.read_csv(path, low_memory=False)
    xy = f["gridpoint"].str.extract(r"POINT \(([-\d.]+) ([-\d.]+)\)").astype(float)
    f["lon"], f["lat"] = xy[0], xy[1]
    f = f[f["lon"].between(*BOX["lon"]) & f["lat"].between(*BOX["lat"])]
    f = f.drop_duplicates("gridpoint")                                       # a border point is listed per watershed
    cols = [c for c in f.columns if c.startswith("qpe15min_")]
    v = f[cols].to_numpy(dtype=float).T                                      # (period, point), inches per 15 min
    masks = region_weights(f["lon"].to_numpy(), f["lat"].to_numpy())
    means = region_means(v, masks)
    idx = pd.to_datetime([c.split("_")[1] for c in cols], format="%Y%m%d%H%M").tz_localize("UTC")   # period end
    return pd.DataFrame(means, index=idx)[REGIONS]


def aqpi() -> Path:
    """Every archived AQPI daily file → 15-minute region means → hours (four periods ending :15 … :00, stamped at the
    hour's end; an hour missing any period is missing)."""
    files = sorted(str(p) for p in AQPI_DIR.glob("SURFRIDER_FOUNDATION_qpe15min_*.csv"))
    with ProcessPoolExecutor(8) as ex:
        parts = [p for p in ex.map(_aqpi_file, files, chunksize=4) if p is not None]
    q = pd.concat(parts).sort_index()
    q = q[~q.index.duplicated()]
    (ARCHIVE / "aqpi").mkdir(parents=True, exist_ok=True)
    with gzip.open(ARCHIVE / "aqpi" / "aqpi_sf_15min_regions.csv.gz", "wt") as f:
        q.round(5).to_csv(f, index_label="timestamp_utc")
    return aqpi_hours(q, len(files))


AQPI_MAX_DRY_GAP = 8          # periods (2 hours): the longest blank run read as dry between two dry periods
AQPI_MAX_WET_GAP = 2          # periods: the longest blank run filled on the line between its neighbours (most are one)


def aqpi_fill(q: pd.DataFrame) -> tuple:
    """(hours, {dry, wet, missing}) from 15-minute region means. About 5% of AQPI's periods are blank across the whole
    grid (a gap in the feed, not "no rain": Kyle's rollup, which summed the periods present, holds rain in hours with
    one). A run of up to AQPI_MAX_DRY_GAP blank periods between two dry ones (the nearest periods present on either
    side, both 0) is dry; a run of up to AQPI_MAX_WET_GAP blank periods in rain takes the line between its neighbours
    (244 of the 320 runs are a single period); any longer blank stays missing, and so does an hour holding one. Hours
    are the four periods ending :15 … :00, stamped at the hour's end."""
    grid = pd.date_range(q.index.min(), q.index.max(), freq="15min")
    q = q.reindex(grid)
    run = q["city"].isna().astype(int).groupby(q["city"].notna().cumsum()).transform("sum")   # length of each blank run
    short = q["city"].isna() & (run <= AQPI_MAX_DRY_GAP)       # a whole missing day (Oct 7–10, …) stays missing
    dry_gap = q.isna() & q.ffill().eq(0) & q.bfill().eq(0) & short.to_numpy()[:, None]
    q = q.mask(dry_gap, 0.0)
    short_wet = q["city"].isna() & (run <= AQPI_MAX_WET_GAP)
    filled = q.interpolate(limit=AQPI_MAX_WET_GAP, limit_area="inside")   # a one- or two-period gap in rain: its neighbours' line
    q = q.mask(pd.DataFrame({c: short_wet & q[c].isna() for c in q.columns}), filled)
    g = q.groupby(q.index.ceil("h"))
    h = g.sum(min_count=1).where(g.count() == 4)
    return h, {"dry": int(dry_gap["city"].sum()), "wet": int(short_wet.sum()), "missing": int(q["city"].isna().sum())}


def aqpi_hours(q: pd.DataFrame | None = None, n_files: int | None = None) -> Path:
    """aqpi_fill on the archived 15-minute means (or ``q``), written as the hourly file."""
    if q is None:
        q = pd.read_csv(ARCHIVE / "aqpi" / "aqpi_sf_15min_regions.csv.gz", parse_dates=["timestamp_utc"]).set_index("timestamp_utc")
        q.index = pd.to_datetime(q.index, utc=True)
    h, n = aqpi_fill(q)
    print(f"  aqpi: {n_files or '?'} files, {n['dry']:,} dry and {n['wet']:,} wet gaps filled, {n['missing']:,} periods still "
          f"missing; {int(h['city'].notna().sum()):,} of {len(h):,} hours whole")
    return write("aqpi", h)


# ── MRMS ─────────────────────────────────────────────────────────────────────

_FS = None


def _fs():
    """One anonymous S3 connection per worker process."""
    global _FS
    if _FS is None:
        import s3fs
        _FS = s3fs.S3FileSystem(anon=True)
    return _FS


def _mrms_decode(args: tuple) -> tuple:
    """(stamp, {region: inches} | None) for one gzip'd GRIB2 file on S3: decode, cut the city box, average."""
    key, stamp = args
    import eccodes
    fs = _fs()
    try:
        raw = gzip.decompress(fs.cat(key))
    except FileNotFoundError:
        return stamp, None
    gid = eccodes.codes_new_from_message(raw)
    try:
        ni, nj = eccodes.codes_get(gid, "Ni"), eccodes.codes_get(gid, "Nj")
        lat0, lon0 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees"), eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
        dlat, dlon = eccodes.codes_get(gid, "jDirectionIncrementInDegrees"), eccodes.codes_get(gid, "iDirectionIncrementInDegrees")
        vals = eccodes.codes_get_values(gid).reshape(nj, ni)
    finally:
        eccodes.codes_release(gid)
    lats = lat0 - dlat * np.arange(nj)                                       # MRMS runs north → south
    lons = (lon0 - 360 if lon0 > 180 else lon0) + dlon * np.arange(ni)
    jj = np.where((lats >= BOX["lat"][0]) & (lats <= BOX["lat"][1]))[0]
    ii = np.where((lons >= BOX["lon"][0]) & (lons <= BOX["lon"][1]))[0]
    sub = vals[np.ix_(jj, ii)].astype(float)
    sub[sub < 0] = np.nan                                                    # −3: no coverage
    lon, lat = np.meshgrid(lons[ii], lats[jj])
    means = region_means(sub[None], region_weights(lon, lat))
    return stamp, {k: float(means[k][0]) / MM_PER_IN for k in REGIONS}


def _mrms_run(jobs: list, source_kind: str) -> pd.DataFrame:
    rows = {}
    with ProcessPoolExecutor(12) as ex:
        for i, (stamp, got) in enumerate(ex.map(_mrms_decode, jobs, chunksize=8)):
            if got is not None:
                rows[stamp] = got
            if i and i % 500 == 0:
                print(f"  mrms {source_kind}: {i:,} / {len(jobs):,}")
    return pd.DataFrame.from_dict(rows, orient="index")[REGIONS].sort_index()


def mrms_daily(end: str) -> Path:
    """The 24-hour Pass2 total ending at each Pacific midnight (07Z in daylight time, 08Z in standard), every day from
    MRMS_START: one file a day, indexed by the Pacific day it covers."""
    days = pd.date_range(MRMS_START, end, freq="D")
    jobs = []
    for d in days:
        end_utc = (d + pd.Timedelta(days=1)).tz_localize(TZ).tz_convert("UTC")
        k = f"{MRMS_BUCKET}/{MRMS_24H}/{end_utc:%Y%m%d}/MRMS_{MRMS_24H}_{end_utc:%Y%m%d-%H%M%S}.grib2.gz"
        jobs.append((k, d))
    f = _mrms_run(jobs, "daily")
    return write("mrms", f, "daily")


def wet_days(start: str, end: str, min_in: float = 0.01) -> list:
    """Pacific days a gauge, AQPI or MRMS's daily total wet, and their neighbours (a gauge can book a storm a day off)."""
    g = pd.read_csv(FORECAST / "data" / "raw" / "historical_rain.csv", parse_dates=["date"])
    wet = set(g.loc[g["precip_inches"] >= min_in, "date"])
    for src in ("mrms",):
        p = OUT_DIR / f"{src}_daily.csv.gz"
        if p.exists():
            d = read(src, "daily")
            wet |= set(d.index[(d >= min_in).any(axis=1)])
    p = OUT_DIR / "aqpi_hourly.csv.gz"
    if p.exists():
        a = read("aqpi")
        day = a.groupby(a.index.tz_convert(TZ).tz_localize(None).normalize()).sum()
        wet |= set(day.index[(day >= min_in).any(axis=1)])
    wet = {d + pd.Timedelta(days=k) for d in wet for k in (-1, 0, 1)}
    return sorted(d for d in wet if pd.Timestamp(start) <= d <= pd.Timestamp(end))


def mrms_hourly(start: str, end: str, min_in: float = 0.1) -> Path:
    """Every hour of every wet Pacific day (``wet_days`` at ``min_in``: about 500 days of the record at 0.1″, ~12,000
    files) from the 01H Pass2 product, stamped at the hour's end. Dry hours are not fetched: the file holds wet days."""
    jobs = []
    for d in wet_days(start, end, min_in):
        for h in pd.date_range(d.tz_localize(TZ), periods=24, freq="h"):
            e = (h + pd.Timedelta(hours=1)).tz_convert("UTC")
            jobs.append((f"{MRMS_BUCKET}/{MRMS_01H}/{e:%Y%m%d}/MRMS_{MRMS_01H}_{e:%Y%m%d-%H%M%S}.grib2.gz", e))
    print(f"  mrms hourly: {len(jobs):,} hours on wet days {start} → {end}")
    f = _mrms_run(jobs, "hourly")
    f.index = pd.DatetimeIndex(f.index)
    return write("mrms", f)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", choices=["aorc", "aqpi", "aqpi-hours", "mrms-daily", "mrms-hourly"])
    ap.add_argument("--years", default="2010-2025", help="aorc: first-last calendar year")
    ap.add_argument("--start", default=str(MRMS_START.date()))
    ap.add_argument("--end", default="2026-08-31")
    ap.add_argument("--min-in", type=float, default=0.1, help="mrms-hourly: a wet day's least rain at any source")
    a = ap.parse_args(argv)
    if a.source == "aorc":
        y0, y1 = (int(x) for x in a.years.split("-"))
        aorc(list(range(y0, y1 + 1)))
    elif a.source == "aqpi":
        aqpi()
    elif a.source == "aqpi-hours":                      # rebuild the hours from the archived 15-minute means
        aqpi_hours()
    elif a.source == "mrms-daily":
        mrms_daily(a.end)
    else:
        mrms_hourly(a.start, a.end, a.min_in)


if __name__ == "__main__":
    main()
