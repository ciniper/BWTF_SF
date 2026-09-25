#!/usr/bin/env python3
"""
Live CSO Forecast Dashboard

Real-time dashboard that shows:
1. Current CSO risk based on actual observed rainfall
2. 5-day forecast based on ECMWF/GFS weather model predictions
3. Current SFPUC beach status (what's actually posted right now)

Data flow:
  NWS observations (last 7 days) → compute rain features → run trained model → TODAY's probability
  Open-Meteo forecasts (next 5 days) → compute projected rain features → run model → FORECAST probabilities
  SFPUC LIMS API → current beach posting status

Run: python live_dashboard.py
Open: http://localhost:8091
"""

import json
import re
import pickle
import sys
import time
import traceback
import http.server
import socketserver
import numpy as np
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from threading import Thread, Lock
from zoneinfo import ZoneInfo

# Supabase (observed CSO flags from the pg_cron watcher's alert_log).
# Optional: everything degrades to model-only composition without it.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# This module is imported both as the forecaster's root module (page.py puts
# this directory on sys.path) and as features.forecast.live_dashboard (tests);
# the `src.*` imports below need this directory either way.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.stations import STATION_BASINS, STATION_NAMES, STATIONS  # noqa: E402
from shared.zones import ZONES, ZONE_OF_SOURCE, ZONE_OF_STATION  # noqa: E402
from shared.outfalls import FEED_NAME_TO_OUTFALLS, OUTFALLS  # noqa: E402
from shared.datasf import BEACH_SAMPLES_URL  # noqa: E402
from shared.alert_log import ESCALATIONS as _ESCALATIONS, REALTIME_SOURCES  # noqa: E402
from shared.standards import STANDARDS, flag_exceedances, parse_result, single_sample_max  # noqa: E402
try:
    from shared import supabase as _supabase
except Exception:
    _supabase = None

MODEL_DIR = Path(__file__).parent / "data" / "models"
PORT = 8091
LOCAL_TZ = ZoneInfo("America/Los_Angeles")

# Basin / group / zone geography is shared with training (src/models/groups.py)
# so serving can never disagree with what the models were fit on. Every
# registry station maps to a basin model — including 4618 Mission Creek,
# served by the Central model since v4 (2026-09).
from src.models.groups import (  # noqa: E402
    GROUPS_BY_BASIN, OBSERVED_STATION_BASIN, ZONE_GROUPS, zone_risks,
)
from src.models.impact import compose as _compose_risk  # noqa: E402
from src.models.impact import impact_fraction as _impact_fraction  # noqa: E402
from src.models.impact import smooth_table as _smooth_table  # noqa: E402
from src.models.rain_features import add_daily_features as _add_daily_features  # noqa: E402
from src.models.rain_features import hourly_intensity as _hourly_intensity  # noqa: E402
from src.models import scorecard as _sc  # noqa: E402  (one rule for the model-check scorecard, shared with train_v4)
from src.models import candidates as _cand  # noqa: E402  (candidate model sets: scorecards + manifests only, never served live)

# ─── Data source URLs ────────────────────────────────────────────────────────

NWS_OBS_URL = "https://api.weather.gov/stations/KSFO/observations"
# The two NOAA daily gauges the models were TRAINED on (data/raw/historical_rain.csv
# is these exact series). Past complete days are re-based onto them so
# serving sees the same rain the training labels were matched to.
ACIS_URL = "https://data.rcc-acis.org/StnData"
ACIS_GAUGES = {"SF Downtown": "047772", "SF Oceanside": "047767"}
NWS_FORECAST_URL = "https://api.weather.gov/gridpoints/MTR/88,126/forecast/hourly"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
SFPUC_API_URL = "https://infrastructure.sfwater.org/lims.asmx/getBeaches"

# Open-Meteo params for SF.
# Model: ECMWF IFS rather than Open-Meteo's default "best_match" (a GFS/HRRR
# blend in North America). Two reasons (2026-09-04): the stage-1 model was
# trained on ERA5 — ECMWF's reanalysis — so IFS is the like-for-like
# inference source; and best_match reported 0.0 mm for 2026-09-03 while SF
# gauges logged an evening of light rain that IFS did forecast (~5 mm).
# Caveat: Open-Meteo interpolates IFS's 3-hourly precipitation to hourly, so
# the peak-intensity features (rain_max1h/3h) from FORECAST hours are
# smoother than ERA5's native hourly — revisit if a recalibration shows
# forecast-day risk running low. Past hours are overridden with gauge
# observations (see _overlay_observed_rain), so this only affects future days.
METEO_PARAMS = {
    "latitude": 37.7749,
    "longitude": -122.4194,
    "hourly": "precipitation,temperature_2m,wind_speed_10m,wind_direction_10m",
    "past_days": 7,
    "forecast_days": 6,
    "timezone": "America/Los_Angeles",
    "models": "ecmwf_ifs025",
}

# Observed rain for past hours: NWS hourly gauge observations at SFO (KSFO —
# the nearest ASOS gauge to the city; there is none downtown). Weather-model
# output for past days is a hindcast, not a measurement, and it missed real
# rain (2026-09-03) — the dashboard labelled it "observed" anyway.
NWS_HEADERS = {"User-Agent": "bwtf-sf forecast (https://bwtf-sf.vercel.app)",
               "Accept": "application/geo+json"}
NWS_MIN_OBS_PER_DAY = 12   # fewer than this and the gauge day is treated as unknown


def _now_local() -> datetime:
    """Naive local (America/Los_Angeles) 'now', matching Open-Meteo's local
    timestamps. datetime.now() is UTC on Vercel, which is 7-8 hours off."""
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)


def observed_hourly_from_features(features: list) -> dict:
    """{naive local hour -> mm} from NWS observation GeoJSON features.

    precipitationLastHour at an observation time T covers (T-1h, T]; it's
    bucketed to the hour ENDING at or after T so it lines up with Open-Meteo's
    convention (the value at hour H is the sum over the preceding hour).
    Specials and the routine hourly METAR can both report the same water, so
    the max per bucket is kept rather than the sum. KSFO reports null (not 0)
    when dry — callers fill 0 for covered hours with no value.
    """
    out: dict = {}
    for feat in features or []:
        props = feat.get("properties") or {}
        val = (props.get("precipitationLastHour") or {}).get("value")
        ts = props.get("timestamp")
        if val is None or not ts:
            continue
        t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(LOCAL_TZ).replace(tzinfo=None)
        bucket = t.replace(minute=0, second=0, microsecond=0)
        if bucket != t:
            bucket += timedelta(hours=1)
        out[bucket] = max(out.get(bucket, 0.0), float(val))
    return out

# ─── Cached data with thread-safe refresh ─────────────────────────────────────

class LiveData:
    """Thread-safe container for cached live data"""

    def __init__(self):
        self.lock = Lock()
        self.last_refresh = None
        self.rain_history = None       # Last 7 days hourly rain
        self.rain_forecast = None      # Next 5 days hourly rain
        self.current_features = None   # Today's model features
        self.forecast_features = None  # Next 5 days model features
        self.predictions = None        # Model predictions
        self.beach_status = None       # SFPUC beach status
        self.error = None
        self.models = self._load_models()
        self.thresholds = self._load_thresholds()
        self.volume_models = self._load_volume_models()
        self.impact_table = self._load_impact_table()

    def _load_models(self):
        models = {}
        for name in ["citywide", "westside", "north_shore", "central", "southeast"]:
            path = MODEL_DIR / f"{name}_model.pkl"
            if path.exists():
                with open(path, "rb") as f:
                    models[name] = pickle.load(f)
        return models

    def _load_volume_models(self):
        """Per-basin expected-discharge-volume regressors (log1p MG), from the
        v2 retrain on SFPUC-reported events. Used to pick the right
        persistence curve (big discharges keep beaches posted longer)."""
        models = {}
        for name in ["westside", "north_shore", "central", "southeast"]:
            path = MODEL_DIR / f"{name}_volume.pkl"
            if path.exists():
                with open(path, "rb") as f:
                    models[name] = pickle.load(f)
        return models

    def _load_impact_table(self):
        """Empirical P(group bacteria elevated | days since discharge, size),
        measured from beach samples joined to reported discharges
        (train_v4.fit_impact_table), smoothed to a non-increasing decay per
        size class by the shared src/models/impact.smooth_table."""
        path = MODEL_DIR / "impact_table.json"
        if not path.exists():
            return {}
        with open(path) as f:
            return _smooth_table(json.load(f))

    @property
    def rain_sources(self) -> set:
        """Rain series the loaded models were trained on ('avg' or a gauge)."""
        return {md.get("rain_source", "avg") for md in self.models.values()} | \
               {md.get("rain_source", "avg") for md in self.volume_models.values()} | {"avg"} | set(ACIS_GAUGES)

    def _predict_calibrated(self, features_by_source: dict) -> dict:
        """
        Run model prediction with rain-weighted calibration offset.

        ``features_by_source`` maps a rain source ('avg', 'SF Oceanside', ...)
        to that day's feature dict built from that series; each model is
        scored on the source it was trained on (v4 regional models).
        
        The offset removes the model's dry-day noise floor. But when rain IS
        present, the model's prediction is real signal — so we scale the offset
        down proportionally to the 3-day cumulative rain:
          - 0" rain → full offset (suppress noise)
          - 0.25" rain → half offset
          - 0.5"+ rain → no offset (trust the model)
        """
        results = {}
        if not isinstance(features_by_source, dict) or "avg" not in features_by_source:
            features_by_source = {"avg": features_by_source}  # legacy single-dict callers
        for name, model_data in self.models.items():
            features = features_by_source.get(model_data.get("rain_source", "avg"), features_by_source["avg"])
            rain_3d = features.get("rain_3d_cum", 0) or 0
            rain_factor = max(0.0, 1.0 - rain_3d * 2.0)  # full offset at 0", zero at 0.5"+
            model = model_data["model"]
            feat_names = model_data["features"]
            offset = model_data.get("calibration_offset", 0)
            X = pd.DataFrame([{f: features.get(f, 0) for f in feat_names}])
            raw_prob = float(model.predict_proba(X)[0, 1])
            effective_offset = offset * rain_factor
            calibrated = max(0.0, raw_prob - effective_offset)
            results[name] = round(calibrated, 3)
        return results

    def _load_thresholds(self):
        path = MODEL_DIR / "thresholds.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return {}

    # ── Discharge → beach-impact composition ────────────────────────────────
    #
    # The v2 models predict P(discharge TODAY). Beaches stay contaminated for
    # days after a discharge, so the displayed risk for day D composes the
    # discharge probabilities of D and the prior week with how often beaches
    # were still elevated k days after a discharge of that size:
    #
    #   risk(D) = 1 - ∏_{k=0..7} (1 - p_discharge(D-k) · x(k, size(D-k)))
    #
    # with x(0, ·) ≡ 1: a discharge on day D is itself a "stay out" day (the
    # alert system treats an active CSO exactly like a posting), so the
    # same-day term is the discharge probability itself; only k ≥ 1 uses the
    # bacteria-measured decay.
    #
    # x(k, size) is the discharge-ATTRIBUTABLE elevation probability, i.e. the
    # impact table's P(elevated) with the dry-weather background removed via
    # the independent-OR identity  p = 1-(1-baseline)(1-x)  →
    # x = (p - baseline)/(1 - baseline). That keeps dry days at ~0 like before.

    # Site groups per basin: beaches within one basin respond very
    # differently (measured, 2026-09 retrain on the corrected station
    # registry: Ocean Beach and Baker/China both disperse in ~1-2 days —
    # Pacific surf and Golden Gate flushing — while Southeast beaches
    # (Islais, Crane Cove, Candlestick) hold contamination most of a week.
    # The pre-registry "Baker/China holds to day 3" curve was actually
    # Mission Creek/Crane Cove data under mislabeled ids). Basin display =
    # worst group; per-group values in the payload.
    BASIN_IMPACT_GROUPS = GROUPS_BY_BASIN
    _BUCKET_ORDER = ["0", "1", "2", "3", "4-5", "6-7"]

    def _impact_fraction(self, group: str, days_since: int, volume_mg: float) -> float:
        """x(k, size) — see src/models/impact.impact_fraction (shared with training)."""
        return _impact_fraction(self.impact_table, group, days_since, volume_mg)

    def _predict_expected_volumes(self, features_by_source: dict) -> dict:
        """Expected discharge volume (MG) per basin if a discharge happens."""
        out = {}
        if not isinstance(features_by_source, dict) or "avg" not in features_by_source:
            features_by_source = {"avg": features_by_source}
        for name, md in self.volume_models.items():
            features = features_by_source.get(md.get("rain_source", "avg"), features_by_source["avg"])
            X = pd.DataFrame([{f: features.get(f, 0) for f in md["features"]}])
            out[name] = max(0.0, float(np.expm1(md["model"].predict(X)[0])))
        return out

    def _fetch_observed_cso(self, window_start) -> dict:
        """Observed CSO onsets from the Supabase alert_log the pg_cron watcher
        writes (one row per escalation; edge-triggered, so a row marks the
        ONSET day — exactly what the persistence composition needs).

        Returns {date: {basin_key, ...}}. Empty dict when Supabase is not
        configured, the query fails, or nothing was observed — absence of a
        row means "not observed", never "no discharge", so callers fall back
        to model probabilities.
        """
        if _supabase is None or not _supabase.is_configured():
            return {}
        try:
            rows = _supabase.select("alert_log", {
                "select": "created_at,event_type,station_ids,simulated,results",
                "event_type": "eq.cso",
                "simulated": "eq.false",
                "source": f"in.({','.join(REALTIME_SOURCES)})",
                "created_at": f"gte.{window_start.isoformat()}T00:00:00+00:00",
                "order": "created_at.asc",
                "limit": "500",
            })
        except Exception as e:
            print(f"observed-CSO fetch failed (composition falls back to model): {e}")
            return {}

        observed = {}
        for row in rows:
            try:
                ts = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                local_date = ts.astimezone(LOCAL_TZ).date()
            except (KeyError, ValueError):
                continue
            # Prefer per-station transitions (a mixed dispatch can bundle
            # 'posted' escalations with the 'cso' ones); fall back to the
            # row-level station list. `results` is an object in pg_live rows
            # but a bare list in older watcher rows — tolerate both.
            station_ids = []
            results = row.get("results")
            transitions = results.get("transitions") if isinstance(results, dict) else None
            transitions = transitions or []
            for t in transitions:
                # belt-and-braces: the row-level simulated=eq.false filter
                # already excludes any row containing a simulated transition
                # (it's bool_or over transitions), but skip per-transition
                # simulated flags too in case that semantic ever changes
                if isinstance(t, dict) and t.get("to") == "cso" and not t.get("simulated"):
                    station_ids.append(t.get("station_id", ""))
            if not station_ids:
                station_ids = row.get("station_ids") or []
            for sid in station_ids:
                basin = OBSERVED_STATION_BASIN.get(sid)
                if basin:
                    observed.setdefault(local_date, set()).add(basin)
        return observed

    def _compose_impact(self, day_probs: list, day_volumes: list, idx: int,
                        day_dates: list = None, observed: dict = None) -> dict:
        """Composed beach-impact risk for daily-table row `idx`, per basin
        (worst group) plus per-group values under "_groups". Where the
        watcher OBSERVED a CSO onset that day's discharge probability is
        replaced with certainty. Implementation shared with training:
        src/models/impact.compose."""
        composed, groups_out = _compose_risk(self.impact_table, self.BASIN_IMPACT_GROUPS,
                                             day_probs, day_volumes, idx, day_dates, observed)
        # NOTE: keep `composed` flat floats only — the frontend takes
        # Math.max(Object.values(predictions)) and renders a card per key.
        composed["_groups"] = groups_out
        return composed

    @staticmethod
    def _features_from_row(row) -> dict:
        """Model features from one row of the daily rain table (shared by the
        live and historical paths — keep in lockstep with train_v2)."""
        return {
            "precip_avg": row.get("precip_inches", 0) or 0,
            "precip_max": row.get("precip_max_hourly", 0) or 0,
            "rain_max1h": row.get("precip_max_hourly", 0) or 0,
            "rain_max3h": row.get("rain_max3h", 0) or 0,
            "rain_max6h": row.get("rain_max6h", 0) or 0,
            "rain_2d_cum": row.get("rain_2d_cum", 0) or 0,
            "rain_3d_cum": row.get("rain_3d_cum", 0) or 0,
            "rain_5d_cum": row.get("rain_5d_cum", 0) or 0,
            "rain_7d_cum": row.get("rain_7d_cum", 0) or 0,
            "rain_14d_cum": row.get("rain_14d_cum", 0) or 0,
            "rain_30d_cum": row.get("rain_30d_cum", 0) or 0,
            "rain_lag1d": row.get("rain_lag1d", 0) or 0,
            "rain_lag2d": row.get("rain_lag2d", 0) or 0,
            "rain_lag3d": row.get("rain_lag3d", 0) or 0,
            "rain_lag5d": row.get("rain_lag5d", 0) or 0,
            "rain_lag7d": row.get("rain_lag7d", 0) or 0,
            "antecedent_moisture": row.get("antecedent_moisture", 0) or 0,
            "wet_prior_3d": int(row.get("wet_prior_3d", 0) or 0),
            "peak_3d": row.get("peak_3d", 0) or 0,
            "dry_spell_days": row.get("dry_spell_days", 0) or 0,
        }

    def refresh(self):
        """Fetch all live data and run predictions"""
        try:
            # 1. Fetch hourly rain (past 7 days + next 6 days) from Open-Meteo,
            #    then replace past hours with SFO gauge observations
            rain_df = self._overlay_observed_rain(self._fetch_open_meteo())

            # 2. Fetch SFPUC beach status
            beach_status = self._fetch_sfpuc()

            # 3. Compute features and run model for each day
            predictions = self._compute_predictions(rain_df)

            now_local = _now_local()
            with self.lock:
                self.rain_history = rain_df[rain_df["timestamp"] <= now_local].to_dict("records") if rain_df is not None else []
                self.rain_forecast = rain_df[rain_df["timestamp"] > now_local].to_dict("records") if rain_df is not None else []
                self.predictions = predictions
                self.beach_status = beach_status
                self.last_refresh = datetime.now()
                self.error = None

        except Exception as e:
            with self.lock:
                self.error = str(e)
            traceback.print_exc()

    def _fetch_open_meteo(self) -> pd.DataFrame:
        """Fetch hourly precipitation from Open-Meteo (past 7 days + next 6 days)"""
        r = requests.get(OPEN_METEO_URL, params=METEO_PARAMS, timeout=30)
        r.raise_for_status()
        data = r.json()

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        precip = hourly.get("precipitation", [])
        temp = hourly.get("temperature_2m", [])
        wind_speed = hourly.get("wind_speed_10m", [])
        wind_dir = hourly.get("wind_direction_10m", [])

        df = pd.DataFrame({
            "timestamp": pd.to_datetime(times),
            "precip_mm": precip,
            "temp_c": temp,
            "wind_speed_kmh": wind_speed,
            "wind_dir_deg": wind_dir,
        })
        df["precip_inches"] = df["precip_mm"] / 25.4
        return df

    def _fetch_nws_observed_rain(self, start_local: datetime, end_local: datetime) -> pd.Series:
        """Hourly observed precipitation (mm) at KSFO for every COMPLETE local
        hour in [start_local, end_local], indexed by naive local hour.

        Days are fetched in parallel (one request per local day, ~300
        five-minute observations each). A day whose fetch fails or returns
        fewer than NWS_MIN_OBS_PER_DAY observations is left out entirely, so
        its hours stay NaN and the caller keeps the model's values for them —
        a gauge outage must never zero out a real storm.
        """
        from concurrent.futures import ThreadPoolExecutor

        days = pd.date_range(start_local.date(), end_local.date(), freq="D")

        def fetch_day(day):
            d0 = datetime.combine(day.date(), datetime.min.time()).replace(tzinfo=LOCAL_TZ)
            d1 = d0 + timedelta(days=1)
            url, params = NWS_OBS_URL, {
                "start": d0.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": d1.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": 500,
            }
            feats, pages = [], 0
            try:
                while url and pages < 4:
                    r = requests.get(url, params=params if pages == 0 else None,
                                     headers=NWS_HEADERS, timeout=15)
                    r.raise_for_status()
                    j = r.json()
                    feats += j.get("features", [])
                    url = (j.get("pagination") or {}).get("next")
                    pages += 1
            except Exception as exc:  # pragma: no cover - network
                print(f"NWS observations unavailable for {day.date()}: {exc}")
                return day, None
            return day, feats

        with ThreadPoolExecutor(max_workers=8) as pool:
            fetched = list(pool.map(fetch_day, days))

        hourly: dict = {}
        for day, feats in fetched:
            if feats is None or len(feats) < NWS_MIN_OBS_PER_DAY:
                continue
            observed = observed_hourly_from_features(feats)
            first = datetime.combine(day.date(), datetime.min.time()) + timedelta(hours=1)
            for h in pd.date_range(first, first + timedelta(hours=23), freq="h"):
                h = h.to_pydatetime()
                if h < start_local or h > end_local:
                    continue          # outside the window, or the hour isn't complete yet
                hourly[h] = observed.get(h, 0.0)
        return pd.Series(hourly, dtype="float64")

    def _overlay_observed_rain(self, rain_df: pd.DataFrame) -> pd.DataFrame:
        """Replace past hours' model precipitation with gauge observations.

        Adds a ``rain_source`` column: 'observed' (gauge), 'model' (past hour
        with no usable observation — hindcast), or 'forecast' (future hour).
        Any failure leaves the model data intact and logs.
        """
        if rain_df is None or rain_df.empty:
            return rain_df
        rain_df = rain_df.copy()
        now_local = _now_local()
        past = rain_df["timestamp"] <= now_local
        rain_df["rain_source"] = np.where(past, "model", "forecast")
        try:
            obs = self._fetch_nws_observed_rain(rain_df["timestamp"].min().to_pydatetime(), now_local)
        except Exception as exc:  # pragma: no cover - network
            print(f"Observed-rain overlay skipped: {exc}")
            return rain_df
        if obs is None or obs.empty:
            return rain_df
        matched = rain_df["timestamp"].map(obs)
        have = past & matched.notna()
        rain_df.loc[have, "precip_mm"] = matched[have].astype(float).values
        rain_df.loc[have, "precip_inches"] = rain_df.loc[have, "precip_mm"] / 25.4
        rain_df.loc[have, "rain_source"] = "observed"
        return rain_df

    @staticmethod
    def _beach_status(station) -> str:
        """Forecast-page vocabulary (cso / posted / safe / not_sampled / unknown)
        from a shared.sfpuc_api Station. The CLASSIFICATION itself is the
        shared one — the same rules the pg watcher uses (bwtf_classify) — so
        this page can never disagree with the alerts about who is posted.
        Before 2026-09-09 this module had its own colour logic that read a
        posted station with p_color 'G' (e.g. Islais Creek) as "safe"."""
        if station.has_cso:
            return "cso"
        value = getattr(station.status, "value", str(station.status))
        return {"posted": "posted", "safe": "safe", "not_sampled": "not_sampled",
                "not_routinely_sampled": "not_sampled"}.get(value, "unknown")

    def _fetch_sfpuc(self) -> list:
        """Current beach status from SFPUC via the shared client."""
        try:
            from shared.sfpuc_api import SFPUCRealTimeAPI
            stations = SFPUCRealTimeAPI().fetch_stations()
        except Exception as e:
            print(f"SFPUC fetch error: {e}")
            return []
        return [{"name": st.station_name, "status": self._beach_status(st),
                 "has_cso": bool(st.has_cso),
                 "sample_date": st.sample_date.strftime("%m/%d/%y") if getattr(st, "sample_date", None) else None}
                for st in stations]

    # ── Rain → daily features, per rain source ─────────────────────────────

    def _fetch_acis_daily(self, start, end) -> pd.DataFrame:
        """Daily precipitation (inches) for the training gauges over
        [start, end] (dates). Missing/suppressed values are NaN, trace is 0.
        Any failure returns an empty frame — callers keep the hourly series."""
        out = pd.DataFrame({"date": pd.date_range(start, end)})
        for name, sid in ACIS_GAUGES.items():
            try:
                r = requests.post(ACIS_URL, json={"sid": sid, "sdate": str(start), "edate": str(end),
                                                  "elems": [{"name": "pcpn"}]}, timeout=20)
                r.raise_for_status()
                rows = r.json().get("data", [])
            except Exception as exc:  # pragma: no cover - network
                print(f"ACIS {name} unavailable: {exc}")
                rows = []
            vals = {}
            for d, v in rows:
                v = str(v).strip()
                if v in ("M", "S", ""):
                    continue
                if v == "T":
                    vals[pd.Timestamp(d)] = 0.0
                    continue
                m = re.match(r"^([\d.]+)", v)
                if m:
                    vals[pd.Timestamp(d)] = float(m.group(1))
            out[name] = out["date"].map(vals)
        return out

    @staticmethod
    def _add_daily_features(daily: pd.DataFrame) -> pd.DataFrame:
        """Cumulative / lag / antecedent features from `precip_inches` — the
        shared implementation training uses (src/models/rain_features.py)."""
        return _add_daily_features(daily)

    def _daily_frames(self, rain_df: pd.DataFrame, today) -> dict:
        """{rain source: daily feature frame} from the hourly series.

        The hourly series (Open-Meteo model + KSFO gauge overlay + ECMWF
        forecast) supplies every day's intensity features and the totals for
        today and the forecast days. COMPLETE past days are re-based onto the
        NOAA daily gauges the models were trained on: 'avg' = mean of Downtown
        and Oceanside, a gauge source = that gauge (falling back to the other
        one, then to the hourly total — the same fill training used). Each
        day carries a ``rain_source`` label: gauges / observed / mixed /
        model / forecast.
        """
        rain_df = rain_df.copy().sort_values("timestamp").reset_index(drop=True)
        rain_df["date"] = rain_df["timestamp"].dt.date
        agg = dict(precip_inches=("precip_inches", "sum"))
        if "wind_dir_deg" in rain_df:
            agg["wind_dir_avg"] = ("wind_dir_deg", "mean")
        base = rain_df.groupby("date").agg(**agg).reset_index()
        base["date"] = pd.to_datetime(base["date"])
        # peak 1h/3h/6h per day — the shared implementation (windows span midnight)
        base = base.merge(_hourly_intensity(rain_df), on="date", how="left")
        base["precip_max_hourly"] = base["rain_max1h"]
        base = base.sort_values("date").reset_index(drop=True)

        def _day_source(sources):
            kinds = set(sources)
            return kinds.pop() if len(kinds) == 1 else "mixed"
        if "rain_source" in rain_df:
            base["rain_source"] = base["date"].dt.date.map(rain_df.groupby("date")["rain_source"].agg(_day_source))
        else:
            base["rain_source"] = np.where(base["date"].dt.date < today, "model", "forecast")

        past = base["date"].dt.date < today
        acis = pd.DataFrame()
        if past.any():
            acis = self._fetch_acis_daily(base.loc[past, "date"].min().date(), base.loc[past, "date"].max().date())
        gauges = {}
        if not acis.empty:
            for name in ACIS_GAUGES:
                if name in acis:
                    gauges[name] = base["date"].map(acis.set_index("date")[name])

        frames = {}
        for source in self.rain_sources:
            daily = base.copy()
            if gauges:
                if source == "avg":
                    g = pd.concat(gauges.values(), axis=1).mean(axis=1)
                else:
                    g = gauges.get(source, pd.Series(np.nan, index=daily.index))
                    for other in gauges:
                        if other != source:
                            g = g.fillna(gauges[other])
                have = past & g.notna()
                daily.loc[have, "precip_inches"] = g[have].astype(float).values
                daily.loc[have, "rain_source"] = "gauges"
            frames[source] = self._add_daily_features(daily)
        return frames

    def _score_frames(self, frames: dict) -> tuple:
        """Per-day (features_by_source, probs, volumes, dates) over the daily table."""
        n = len(frames["avg"])
        feats = [{src: self._features_from_row(frames[src].iloc[i]) for src in frames} for i in range(n)]
        probs = [self._predict_calibrated(f) for f in feats]
        vols = [self._predict_expected_volumes(f) for f in feats]
        dates = [d.date() for d in frames["avg"]["date"]]
        return feats, probs, vols, dates

    def _day_payload(self, frames: dict, idx: int, feats: list, probs: list, vols: list,
                     dates: list, observed: dict) -> dict:
        day_predictions = self._compose_impact(probs, vols, idx, dates, observed)
        impact_groups = day_predictions.pop("_groups", {})
        f = feats[idx]["avg"]
        row = frames["avg"].iloc[idx]
        return {
            "rain_inches": round(f["precip_avg"], 3),
            "rain_by_gauge": {src: round(float(frames[src].iloc[idx]["precip_inches"]), 3)
                              for src in frames if src != "avg"},
            "rain_source": row.get("rain_source", "model"),
            "rain_2d_cum": round(f["rain_2d_cum"], 3),
            "rain_3d_cum": round(f["rain_3d_cum"], 3),
            "predictions": day_predictions,
            "impact_groups": impact_groups,
            "zones": zone_risks(impact_groups),
            "discharge_probs": probs[idx],
            "observed_cso": sorted(observed.get(dates[idx], ())),
            "features": f,
        }

    def _compute_predictions(self, rain_df: pd.DataFrame) -> dict:
        """CSO / beach-impact predictions for today and the next 5 days. (Past
        days still feed the persistence composition; they are just not
        emitted — the live view is a forecast, "What happened" is the past.)"""
        if rain_df is None or rain_df.empty:
            return {}
        now = _now_local()
        today = now.date()
        frames = self._daily_frames(rain_df, today)
        feats, probs, vols, dates = self._score_frames(frames)
        observed = self._fetch_observed_cso(min(dates)) if dates else {}

        results = {}
        for day_offset in range(0, 6):
            target_date = today + timedelta(days=day_offset)
            if target_date not in dates:
                continue
            idx = dates.index(target_date)
            label = target_date.strftime("%a %b %d")
            if day_offset == 0:
                label = "Today"
            elif day_offset == 1:
                label = "Tomorrow"
            payload = self._day_payload(frames, idx, feats, probs, vols, dates, observed)
            payload.update({"label": label, "date": str(target_date), "day_offset": day_offset,
                            "is_forecast": day_offset > 0, "is_today": day_offset == 0})
            results[str(target_date)] = payload
        return results

    def get_historical(self, date_str: str) -> dict:
        """Hindcast for a historical date ± 5 days: what the CURRENT models say
        given the rain that fell (Open-Meteo archive hourly for intensity,
        NOAA daily gauges for totals). Compare with get_actuals() for what
        actually happened."""
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return {"error": f"Invalid date: {date_str}"}

        start = target - timedelta(days=14)
        end = min(target + timedelta(days=5), datetime.now())
        params = {
            "latitude": 37.7749, "longitude": -122.4194,
            "hourly": "precipitation,temperature_2m,wind_speed_10m,wind_direction_10m",
            "start_date": start.strftime("%Y-%m-%d"), "end_date": end.strftime("%Y-%m-%d"),
            "timezone": "America/Los_Angeles",
        }
        try:
            if (datetime.now() - end).days > 5:
                url = "https://archive-api.open-meteo.com/v1/archive"
            else:
                url = OPEN_METEO_URL
                params["models"] = METEO_PARAMS["models"]
                params["past_days"] = (datetime.now() - start).days
                params["forecast_days"] = max(1, (end - datetime.now()).days + 1)
                del params["start_date"]
                del params["end_date"]
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return {"error": f"Failed to fetch weather data: {e}"}

        hourly = data.get("hourly", {})
        times, precip = hourly.get("time", []), hourly.get("precipitation", [])
        if not times:
            return {"error": "No weather data returned"}
        rain_df = pd.DataFrame({"timestamp": pd.to_datetime(times), "precip_mm": precip})
        rain_df["precip_inches"] = rain_df["precip_mm"] / 25.4
        rain_df["rain_source"] = "model"

        frames = self._daily_frames(rain_df, _now_local().date())
        feats, probs, vols, dates = self._score_frames(frames)
        observed = self._fetch_observed_cso(min(dates)) if dates else {}

        target_date = target.date()
        filtered = {}
        for idx, row_date in enumerate(dates):
            offset = (row_date - target_date).days
            if offset < -2 or offset > 5:
                continue
            payload = self._day_payload(frames, idx, feats, probs, vols, dates, observed)
            payload.update({"label": row_date.strftime("%a %b %d") + (" ★" if offset == 0 else ""),
                            "date": str(row_date), "day_offset": offset, "is_today": offset == 0,
                            "is_forecast": False})
            filtered[str(row_date)] = payload
        return {"target_date": date_str, "predictions": filtered}

    # ── What happened (actuals) and model check (scorecard) ────────────────

    _CSD_DIR = Path(__file__).parent / "data" / "csd"
    _POOBOT_DIR = Path(__file__).parent / "data" / "poobot"
    DATASF_FLOOR = "2020-07-27"   # earliest DataSF bacteria sample
    WATCHER_SINCE = "2026-08-20"  # pg_cron watcher has logged real SFPUC postings / CSO flags since

    def _events_table(self) -> pd.DataFrame:
        """Reported discharges with the beaches they post (via the outfall
        registry): CIWQS self-monitoring events (Oct 2016 → the last month in
        the coverage grid; re-harvested quarterly) plus
        SFPUC feed onsets from the 2016-17 Poo Bot archive. Cached."""
        if getattr(self, "_events_cache", None) is not None:
            return self._events_cache
        by_sfpuc = {s.sfpuc_id: sid for sid, s in STATIONS.items()}
        rows = []
        ev_path = self._CSD_DIR / "sf_csd_events.csv"
        if ev_path.exists():
            ev = pd.read_csv(ev_path, parse_dates=["event_date"])
            for _, r in ev.iterrows():
                o = OUTFALLS.get(r["outfall_id"])
                sids = [by_sfpuc[x] for x in (o.stations if o else ()) if x in by_sfpuc]
                rows.append({"date": r["event_date"].normalize(), "outfall_id": r["outfall_id"],
                             "name": o.name if o else r.get("outfall_name", ""),
                             "basin": o.basin if o else None, "volume_mg": float(r["volume_MG"]) if pd.notna(r["volume_MG"]) else None,
                             "duration_min": float(r["duration_min"]) if pd.notna(r.get("duration_min")) else None,
                             "stations": sids, "source": "CIWQS report"})
        on_path = self._POOBOT_DIR / "discharge_onsets.csv"
        if on_path.exists():
            on = pd.read_csv(on_path, parse_dates=["date"])
            for _, r in on[on["mapped"] == True].iterrows():  # noqa: E712
                oids = [x for x in str(r["outfall_ids"]).split("|") if x in OUTFALLS]
                sids = sorted({by_sfpuc[x] for oid in oids for x in OUTFALLS[oid].stations if x in by_sfpuc})
                rows.append({"date": r["date"].normalize(), "outfall_id": "|".join(oids), "name": r["structure"].title(),
                             "basin": OUTFALLS[oids[0]].basin if oids else None, "volume_mg": None, "duration_min": None,
                             "stations": sids, "source": "SFPUC feed (Poo Bot archive)"})
        self._events_cache = pd.DataFrame(rows)
        return self._events_cache

    def _coverage(self) -> dict:
        """{facility prefix: set of (year, month)} with trustworthy CIWQS labels."""
        if getattr(self, "_coverage_cache", None) is not None:
            return self._coverage_cache
        out = {"Oceanside": set(), "Southeast": set()}
        path = self._CSD_DIR / "sf_csd_monthly_coverage.csv"
        if path.exists():
            cov = pd.read_csv(path)
            ok = cov["status"].isin({"events_parsed", "table_present_zero_events", "no_table_stated_no_discharge"})
            for _, r in cov[ok].iterrows():
                for k in out:
                    if str(r["facility"]).startswith(k):
                        out[k].add((int(r["year"]), int(r["month"])))
        # the 2016-17 feed archive covers both facilities
        for d in pd.date_range("2016-03-19", "2017-01-10", freq="MS"):
            out["Oceanside"].add((d.year, d.month)); out["Southeast"].add((d.year, d.month))
        self._coverage_cache = out
        return out

    ESCALATIONS = _ESCALATIONS

    @staticmethod
    def _escalations_from_rows(rows: list, end_date) -> list:
        """Per-station ESCALATIONS (→ posted / → cso) from alert_log rows, by
        local date. Everything else is dropped: clear-downs (→ ok), and any
        transition flagged simulated. Note the dispatcher logs the clear-down
        of a simulated event with simulated=false (there is nothing simulated
        about the feed going back to green), so filtering on the row flag
        alone would still surface a "simulation" as activity — hence the
        direction filter here."""
        out, seen = [], set()
        for row in rows:
            if row.get("event_type") not in LiveData.ESCALATIONS:
                continue
            if row.get("source") is not None and row["source"] not in REALTIME_SOURCES:
                continue  # manual button dispatches / the retired observer thread
            try:
                d = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")).astimezone(LOCAL_TZ).date()
            except (KeyError, ValueError):
                continue
            if d > end_date:
                continue
            results = row.get("results")
            trans = (results.get("transitions") if isinstance(results, dict) else None) or []
            if not trans:
                trans = [{"station_id": sid, "to": row.get("event_type")} for sid in (row.get("station_ids") or [])]
            for t in trans:
                if not isinstance(t, dict) or t.get("simulated") or t.get("to") not in LiveData.ESCALATIONS:
                    continue
                sid = str(t.get("station_id", ""))
                if (str(d), sid, t.get("to")) in seen:
                    continue  # two writers logged the same escalation (parallel-run era)
                seen.add((str(d), sid, t.get("to")))
                st = next((s for s in STATIONS.values() if s.sfpuc_id == sid), None)
                out.append({"date": str(d), "station_id": sid, "station_name": st.name if st else t.get("station_name", sid),
                            "zone": ZONE_OF_STATION.get(sid), "to": t.get("to")})
        return out

    def _alert_log_transitions(self, start_date, end_date) -> list:
        """Real (non-simulated) posted / CSO escalations the watcher logged."""
        if _supabase is None or not _supabase.is_configured():
            return []
        try:
            rows = _supabase.select("alert_log", {
                "select": "created_at,event_type,station_ids,simulated,results,source",
                "simulated": "eq.false",
                "source": f"in.({','.join(REALTIME_SOURCES)})",
                "created_at": f"gte.{start_date.isoformat()}T00:00:00+00:00",
                "order": "created_at.asc", "limit": "500"})
        except Exception as e:
            print(f"alert_log fetch failed: {e}")
            return []
        return self._escalations_from_rows(rows, end_date)

    def _samples_window(self, start_date, end_date) -> list:
        """Per-sample bacteria results in [start, end]: DataSF for dates from
        2020-07-27, the Poo Bot archive (Dec 2015 – Jan 2017) before that."""
        recs = []
        if str(end_date) >= self.DATASF_FLOOR:
            try:
                r = requests.get(BEACH_SAMPLES_URL, params={
                    "$limit": 5000, "$order": "sample_date ASC",
                    "$where": f"sample_date >= '{start_date}T00:00:00' AND sample_date <= '{end_date}T23:59:59' AND analyte IS NOT NULL",
                }, timeout=30)
                r.raise_for_status()
                for rec in r.json():
                    recs.append((rec.get("source", ""), rec.get("sample_date", "")[:10], rec.get("analyte", ""), rec.get("data", "")))
            except Exception as e:
                print(f"DataSF samples fetch failed: {e}")
        if str(start_date) < self.DATASF_FLOOR:
            path = self._POOBOT_DIR / "samples.csv"
            if path.exists():
                pb = pd.read_csv(path, dtype=str)
                pb = pb[(pb["sample_date"] >= str(start_date)) & (pb["sample_date"] <= str(end_date))]
                for _, r in pb.iterrows():
                    recs.append((r["source"], r["sample_date"], r["analyte"], r["data"]))
        out, seen = [], set()
        for station, d, analyte, raw in recs:
            if station not in STATIONS or analyte not in STANDARDS or (station, d, analyte) in seen:
                continue
            seen.add((station, d, analyte))
            out.append({"date": d, "station": station, "station_name": STATION_NAMES[station],
                        "zone": ZONE_OF_SOURCE[station], "analyte": analyte, "value": parse_result(raw), "value_raw": raw})
        return flag_exceedances(out)  # shared rule, incl. the total-coliform ratio

    def get_actuals(self, date_str: str) -> dict:
        """What actually happened around a date (−2 … +5 days), per zone:
        gauge rain, reported discharges and the beaches they post, watcher-
        logged postings, and bacteria samples."""
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return {"error": f"Invalid date: {date_str}"}
        start, end = target - timedelta(days=2), min(target + timedelta(days=5), _now_local().date())
        dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]

        acis = self._fetch_acis_daily(start, end)
        ev = self._events_table()
        ev = ev[(ev["date"] >= pd.Timestamp(start)) & (ev["date"] <= pd.Timestamp(end))] if not ev.empty else ev
        postings = self._alert_log_transitions(start, end) if str(end) >= "2026-08-01" else []
        samples = self._samples_window(start, end)
        cov = self._coverage()
        cov_end = {k: f"{max(v)[0]}-{max(v)[1]:02d}" if v else None for k, v in cov.items()}

        days = {}
        for d in dates:
            key = str(d)
            rain = {}
            if not acis.empty:
                row = acis[acis["date"] == pd.Timestamp(d)]
                if len(row):
                    rain = {g: (round(float(row.iloc[0][g]), 2) if pd.notna(row.iloc[0][g]) else None) for g in ACIS_GAUGES}
            dis = [] if ev.empty else [
                {"outfall_id": r["outfall_id"], "name": r["name"], "basin": r["basin"], "volume_mg": r["volume_mg"],
                 "duration_min": r["duration_min"], "source": r["source"],
                 "stations": [STATION_NAMES[x] for x in r["stations"]],
                 "zones": sorted({ZONE_OF_SOURCE[x] for x in r["stations"]})}
                for _, r in ev[ev["date"] == pd.Timestamp(d)].iterrows()]
            posts = [p for p in postings if p["date"] == key]
            smp = [x for x in samples if x["date"] == key]
            ocean_cov = (d.year, d.month) in cov["Oceanside"]
            bay_cov = (d.year, d.month) in cov["Southeast"]
            watcher_live = key >= self.WATCHER_SINCE
            zones = {}
            for zk, z in ZONES.items():
                covered = (ocean_cov if zk in ("ocean", "baker_china") else bay_cov) or watcher_live
                z_dis = [x for x in dis if zk in x["zones"]]
                z_cso_posts = [p for p in posts if p["zone"] == zk and p["to"] == "cso"]
                z_posts = [p for p in posts if p["zone"] == zk and p["to"] == "posted"]
                z_smp = [x for x in smp if x["zone"] == zk]
                elevated = sorted({x["station_name"] for x in z_smp if x["exceeds"]})
                discharge = bool(z_dis or z_cso_posts) if covered else None
                zones[zk] = {"discharge": discharge,
                             "discharge_stations": sorted({n for x in z_dis for n in x["stations"] if n in [STATIONS[s].name for s in z.source_ids]} | {p["station_name"] for p in z_cso_posts}),
                             "posted": bool(z_posts) if watcher_live else None,
                             "posted_stations": sorted({p["station_name"] for p in z_posts}),
                             "elevated": (bool(elevated) if z_smp else None), "elevated_stations": elevated,
                             "n_samples": len(z_smp),
                             "bad": (True if (discharge or elevated or z_posts) else (False if (discharge is False and (z_smp or not watcher_live)) else None))}
            days[key] = {"date": key, "rain": rain, "discharges": dis, "postings": posts, "samples": smp, "zones": zones,
                         "coverage": {"oceanside": ocean_cov, "bayside": bay_cov, "watcher": watcher_live}}
        def mon(ym):
            return datetime.strptime(ym, "%Y-%m").strftime("%b %Y") if ym else "?"
        return {"target_date": date_str, "days": days,
                "coverage_end": {"oceanside": cov_end["Oceanside"], "bayside": cov_end["Southeast"]},
                "watcher_since": self.WATCHER_SINCE,
                "sources": {"rain": "NOAA daily gauges (Downtown 047772, Oceanside 047767) via ACIS",
                            "discharges": f"SFPUC CIWQS self-monitoring reports (Oct 2016 – {mon(cov_end['Southeast'])}); "
                                          f"SFPUC feed archive (Mar 2016 – Jan 2017); BWTF watcher ({mon(self.WATCHER_SINCE[:7])} →)",
                            "bacteria": "DataSF beach samples (Jul 2020 →); SFPUC feed archive (Dec 2015 – Jan 2017)"}}

    def _scorecard(self, model: str | None = None) -> dict:
        """The served artifact (model=None) or a candidate set's (by name);
        cached per model for the process lifetime."""
        cache = getattr(self, "_scorecard_cache", None)
        if not isinstance(cache, dict) or "_by_model" not in cache:
            cache = {"_by_model": {}}
            self._scorecard_cache = cache
        key = model or ""
        if key not in cache["_by_model"]:
            import gzip
            if model:
                sc = _cand.load_scorecard(model) if _cand.valid_name(model) else {}
            else:
                path = MODEL_DIR / "scorecard.json.gz"
                sc = {}
                if path.exists():
                    with gzip.open(path, "rt") as f:
                        sc = json.load(f)
            if sc:
                sc["_by_date"] = {d["date"]: d for d in sc.get("days", [])}
            cache["_by_model"][key] = sc
        return cache["_by_model"][key]

    def list_models(self) -> list:
        """The served set first, then every candidate set on disk."""
        out = [{"key": "", "label": "v4 (served)", "family": "gb", "served": True}]
        for m in _cand.list_candidates():
            out.append({"key": m["name"], "label": m["name"], "family": m.get("family"), "note": m.get("note", ""),
                        "created_at": m.get("created_at"), "rain_sources": m.get("rain_sources"), "served": False})
        return out

    def get_scorecard(self, date_str: str, start: str | None = None, end: str | None = None,
                      model: str | None = None) -> dict:
        """Model check: the training-time hindcast (final model and the
        holdout-fit model) next to the labels, for −2 … +5 days around a date,
        plus a scorecard over a window of the hindcast (``window``).

        The window is the explicit ``start``/``end`` when given (clamped to
        the artifact span), else the rain season the date falls in, else —
        for dates outside the artifact — the post-training stretch (days the
        served models never saw, appended by ``train_v4.py --rescore``) or,
        failing that, the whole holdout. Each day is scored with the holdout-
        fit probability where one exists, else the served model's; the
        response grades the mix (``window.grade``: holdout / post_training /
        out_of_sample / in_sample / mixed)."""
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return {"error": f"Invalid date: {date_str}"}
        for v in (start, end):
            if v:
                try:
                    datetime.strptime(v, "%Y-%m-%d")
                except ValueError:
                    return {"error": f"Invalid date: {v}"}
        if model and not _cand.valid_name(model):
            return {"error": f"Unknown model set: {model}"}
        sc = self._scorecard(model)
        if not sc:
            return {"error": f"scorecard artifact missing for {model!r} (leaderboard.py --save)" if model
                    else "scorecard artifact missing (run train_v4.py)"}
        around = {}
        for i in range(-2, 6):
            k = str(target + timedelta(days=i))
            if k in sc["_by_date"]:
                around[k] = sc["_by_date"][k]

        span = sc.get("span") or [sc["days"][0]["date"], sc["days"][-1]["date"]]
        holdout_start = sc.get("holdout_start")
        trained_through = sc.get("trained_through") or span[1]
        post_start = _sc._next_day(trained_through) if trained_through < span[1] else None
        if start or end:
            preset = "custom"
            lo, hi = _sc.clamp_window(start, end, span)
        elif span[0] <= date_str <= span[1]:
            preset = "season"
            lo, hi = _sc.clamp_window(*_sc.season_bounds(_sc.season_of(date_str)), span)
        elif post_start:
            preset = "post"
            lo, hi = post_start, span[1]
        else:
            preset = "holdout"
            lo, hi = _sc.clamp_window(holdout_start, trained_through, span)
        zone_keys = list(ZONES)
        days = sc["days"]
        summary = _sc.window_summary(days, zone_keys, lo, hi)
        window = {"preset": preset, "season": _sc.season_of(lo) if preset == "season" else None,
                  "holdout_only": summary["grade"] == "holdout", **summary, "start": lo, "end": hi,
                  "zone_confusion": _sc.zone_confusion(days, zone_keys, lo, hi, holdout_only=False, thresholds=_sc.LINE_GRID),
                  "basins": _sc.basin_metrics(days, start=lo, end=hi)}

        eval_path = MODEL_DIR / "eval_report.json"
        ev = json.loads(eval_path.read_text()) if eval_path.exists() else {}
        targets = {k: {"n_events": t.get("n_events"), "holdout": t.get("holdout"), "rain_source": t.get("rain_source")}
                   for k, t in ev.get("targets", {}).items()}
        return {"target_date": date_str, "in_span": bool(around), "span": span,
                "model": model or "", "models": self.list_models(),
                "holdout_start": holdout_start, "trained_at": sc.get("trained_at"),
                "trained_through": trained_through, "post_start": post_start, "rescored_at": sc.get("rescored_at"),
                "basins": sc.get("basins"), "zones": sc.get("zones"), "groups": sc.get("groups"),
                "days": around, "zone_confusion_holdout": sc.get("zone_confusion_holdout"),
                "window": window, "seasons": _sc.seasons_in(span, holdout_start, trained_through),
                "targets": targets, "backtest": ev.get("backtest")}

    def get_bacteria_ground_truth(self, date_str: str) -> dict:
        """
        Fetch actual bacteria samples from SF Gov API for a date range
        matching the historical timeline (target ± 2 days).
        
        Returns per-station, per-date exceedance data grouped by basin,
        so we can overlay "what actually happened" on the model predictions.
        """
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return {"error": f"Invalid date: {date_str}"}

        # Match the historical timeline window: -2 to +5 days
        start = (target - timedelta(days=2)).strftime("%Y-%m-%dT00:00:00")
        end = (target + timedelta(days=5)).strftime("%Y-%m-%dT23:59:59")

        # Station names/basins come from shared.stations; standards from shared.standards.
        try:
            params = {
                "$limit": 5000,
                "$order": "sample_date ASC",
                "$where": f"sample_date >= '{start}' AND sample_date <= '{end}' AND analyte IS NOT NULL",
            }
            r = requests.get(BEACH_SAMPLES_URL,
                             params=params, timeout=30)
            r.raise_for_status()
            raw = r.json()
        except Exception as e:
            return {"error": f"Failed to fetch bacteria data: {e}"}

        if not raw:
            return {
                "target_date": date_str,
                "has_data": False,
                "message": "No bacteria samples found for this date range",
                "days": {},
            }

        # Parse into structured records; "over standard" via the shared rule
        records = []
        for rec in raw:
            station = rec.get("source", "")
            analyte = rec.get("analyte", "")
            value_str = rec.get("data", "")
            sample_date = rec.get("sample_date", "")[:10]
            if not station or not analyte or not sample_date:
                continue
            records.append({
                "date": sample_date,
                "station": station,
                "station_name": STATION_NAMES.get(station, station),
                "basin": STATION_BASINS.get(station, "Unknown"),
                "analyte": analyte,
                "value": parse_result(value_str),
                "value_raw": value_str,
            })
        flag_exceedances(records)

        # Group by date → basin → station
        days = {}
        for rec in records:
            d = rec["date"]
            if d not in days:
                days[d] = {"date": d, "stations": {}, "summary": {}}

            station_key = rec["station"]
            if station_key not in days[d]["stations"]:
                days[d]["stations"][station_key] = {
                    "station": station_key,
                    "station_name": rec["station_name"],
                    "basin": rec["basin"],
                    "samples": [],
                    "has_exceedance": False,
                    "max_entero": None,
                }

            days[d]["stations"][station_key]["samples"].append({
                "analyte": rec["analyte"],
                "value": rec["value"],
                "value_raw": rec["value_raw"],
                "exceeds": rec["exceeds"],
                "threshold": rec["threshold"],
            })

            if rec["exceeds"]:
                days[d]["stations"][station_key]["has_exceedance"] = True

            if rec["analyte"] == "ENTERO" and rec["value"] is not None:
                current_max = days[d]["stations"][station_key]["max_entero"]
                if current_max is None or rec["value"] > current_max:
                    days[d]["stations"][station_key]["max_entero"] = rec["value"]

        # Compute per-day summaries
        for d, day_data in days.items():
            stations_list = list(day_data["stations"].values())
            total_stations = len(stations_list)
            elevated_stations = sum(1 for s in stations_list if s["has_exceedance"])
            total_samples = sum(len(s["samples"]) for s in stations_list)
            elevated_samples = sum(
                sum(1 for samp in s["samples"] if samp["exceeds"])
                for s in stations_list
            )

            # Per-basin breakdown
            basin_summary = {}
            for s in stations_list:
                b = s["basin"]
                if b not in basin_summary:
                    basin_summary[b] = {"total": 0, "elevated": 0, "stations": []}
                basin_summary[b]["total"] += 1
                if s["has_exceedance"]:
                    basin_summary[b]["elevated"] += 1
                basin_summary[b]["stations"].append({
                    "station": s["station"],
                    "name": s["station_name"],
                    "elevated": s["has_exceedance"],
                    "max_entero": s["max_entero"],
                    "sample_count": len(s["samples"]),
                    "exceedance_count": sum(1 for samp in s["samples"] if samp["exceeds"]),
                })

            elevated_basins = sum(1 for b in basin_summary.values() if b["elevated"] > 0)
            likely_cso = elevated_stations >= 3 and elevated_basins >= 2

            day_data["summary"] = {
                "total_stations": total_stations,
                "elevated_stations": elevated_stations,
                "total_samples": total_samples,
                "elevated_samples": elevated_samples,
                "elevated_basins": elevated_basins,
                "likely_cso": likely_cso,
                "basins": basin_summary,
            }

            # Remove the raw stations dict (we have it in basin_summary now)
            del day_data["stations"]

        return {
            "target_date": date_str,
            "has_data": True,
            "sample_count": len(records),
            "days": days,
        }

    def get_snapshot(self) -> dict:
        """Get current state as JSON-serializable dict"""
        with self.lock:
            return {
                "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
                "predictions": self.predictions or {},
                "beach_status": self.beach_status or [],
                "error": self.error,
                "thresholds": self.thresholds,
            }


# ─── Background refresh thread ───────────────────────────────────────────────

def refresh_loop(live_data: LiveData, interval_seconds: int = 1800):
    """Refresh data every 30 minutes"""
    while True:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Refreshing live data...")
        live_data.refresh()
        if live_data.error:
            print(f"  ⚠️ Error: {live_data.error}")
        else:
            print(f"  ✅ Data refreshed. {len(live_data.predictions or {})} days of predictions.")
        time.sleep(interval_seconds)


# ─── HTTP Handler ─────────────────────────────────────────────────────────────

# Global live data instance
LIVE = LiveData()


class LiveDashboardHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_html(self.generate_html())
        elif parsed.path == "/api/data":
            self.send_json(LIVE.get_snapshot())
        elif parsed.path == "/api/refresh":
            LIVE.refresh()
            self.send_json(LIVE.get_snapshot())
        elif parsed.path == "/api/historical":
            date_str = params.get("date", [""])[0]
            if date_str:
                self.send_json(LIVE.get_historical(date_str))
            else:
                self.send_json({"error": "Missing ?date=YYYY-MM-DD parameter"})
        elif parsed.path == "/api/bacteria":
            date_str = params.get("date", [""])[0]
            if date_str:
                self.send_json(LIVE.get_bacteria_ground_truth(date_str))
            else:
                self.send_json({"error": "Missing ?date=YYYY-MM-DD parameter"})
        else:
            self.send_error(404)

    def send_html(self, html):
        data = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj):
        data = json.dumps(obj, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def generate_html(self):
        data_start = "2020-07-27"  # Earliest bacteria data in SF Gov API
        today_str = datetime.now().strftime("%Y-%m-%d")
        html = HTML_TEMPLATE.replace("__MIN_DATE__", data_start).replace("__MAX_DATE__", today_str).replace("__DEFAULT_DATE__", today_str)
        return html


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SF CSO Live Forecast</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Avenir Next','Trebuchet MS','Segoe UI', Roboto, sans-serif; background: #e2e8ee; color: #26272a; min-height: 100vh; }
.container { max-width: 1200px; margin: 0 auto; padding: 20px; }

header { background: #1f6fb0; color: #fff; border-radius: 18px; padding: 28px 32px; margin-bottom: 20px; text-align: center; }
header h1 { font-size: 1.8em; color: #fff; -webkit-text-stroke: 2px #26272a; paint-order: stroke fill; }
header .subtitle { color: rgba(255,255,255,0.85); font-size: 0.9em; margin-top: 4px; }
header .live-dot { display: inline-block; width: 8px; height: 8px; background: #3c9160; border-radius: 50%; margin-right: 6px; animation: blink 2s infinite; }

.mode-bar { display: flex; justify-content: center; align-items: center; gap: 15px; margin-bottom: 20px; flex-wrap: wrap; }
.mode-btn { padding: 8px 20px; border-radius: 8px; border: 2px solid #dfe5ea; background: #ffffff; color: #5e6a71; cursor: pointer; font-size: 0.9em; font-weight: 600; transition: all 0.2s; }
.mode-btn.active { border-color: #1f6fb0; color: #fff; background: #1f6fb0; }
.mode-btn:hover { border-color: #2b7fbf; }
.date-picker { background: #ffffff; border: 2px solid #dfe5ea; border-radius: 8px; padding: 8px 14px; color: #26272a; font-size: 0.9em; cursor: pointer; }
.date-picker:focus { border-color: #2b7fbf; outline: none; }
.historical-label { color: #b97e00; font-size: 0.85em; font-weight: 600; }

.risk-banner { text-align: center; padding: 16px 22px; border-radius: 14px; margin-bottom: 20px; font-size: 1.2em; font-weight: 600; }
.risk-none { background: #3c9160; color: #fff; }
.risk-low { background: #b97e00; color: #fff; }
.risk-moderate { background: #d4763a; color: #fff; }
.risk-high { background: #d15c5c; color: #fff; }
.risk-extreme { background: #b5310a; color: #fff; animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1;} 50%{opacity:0.7;} }

/* Forecast timeline */
.timeline { display: grid; grid-template-columns: repeat(7, 1fr); gap: 10px; margin-bottom: 20px; }
@media (max-width: 800px) { .timeline { grid-template-columns: repeat(4, 1fr); } }
.day-card { background: #ffffff; border-radius: 12px; padding: 14px; text-align: center; border: 2px solid transparent; transition: border-color 0.3s, transform 0.2s; cursor: pointer; }
.day-card.today { border-color: #2b7fbf; }
.day-card.selected { border-color: #b97e00; background: #fff7e6; }
.day-card:hover { transform: translateY(-2px); border-color: #8a949b; }
.day-card.selected:hover { border-color: #b97e00; }
.day-label { font-size: 0.8em; color: #5e6a71; margin-bottom: 4px; }
.day-label.today-label { color: #2b7fbf; font-weight: 700; }
.day-rain { font-size: 0.85em; color: #8a949b; margin-bottom: 8px; }
.day-rain .amount { color: #2b7fbf; font-weight: 600; }
.day-prob { font-size: 2em; font-weight: 700; }
.day-risk { font-size: 0.7em; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }
.day-bar { height: 4px; border-radius: 2px; background: #dfe5ea; margin-top: 8px; overflow: hidden; }
.day-bar-fill { height: 100%; border-radius: 2px; transition: width 0.5s; }
.day-source { font-size: 0.65em; color: #8a949b; margin-top: 6px; }

/* Basin detail */
.basins { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 20px; }
@media (max-width: 700px) { .basins { grid-template-columns: 1fr; } }
.basin-card { background: #ffffff; border-radius: 12px; padding: 16px; border-left: 4px solid #dfe5ea; }
.basin-name { font-size: 0.9em; color: #5e6a71; margin-bottom: 6px; }
.basin-prob { font-size: 1.8em; font-weight: 700; }
.basin-bar { height: 6px; border-radius: 3px; background: #dfe5ea; margin-top: 8px; overflow: hidden; }
.basin-bar-fill { height: 100%; border-radius: 3px; transition: width 0.5s; }
.basin-detail { font-size: 0.8em; color: #8a949b; margin-top: 8px; }

/* Beach status */
.beach-section { background: #ffffff; border-radius: 12px; padding: 20px; margin-bottom: 20px; }
.beach-section h2 { color: #2b7fbf; font-size: 1.1em; margin-bottom: 12px; }
.beach-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; }
.beach-item { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 6px; font-size: 0.85em; background: #eef1f4; }
.beach-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.beach-dot.safe { background: #3c9160; }
.beach-dot.posted { background: #d4763a; }
.beach-dot.cso { background: #d15c5c; animation: blink 1.5s infinite; }
.beach-dot.not_sampled { background: #8a949b; }

/* Ground truth overlay (historical mode) */
.ground-truth { background: #ffffff; border-radius: 12px; padding: 20px; margin-bottom: 20px; border: 2px solid #dfe5ea; }
.ground-truth.has-cso { border-color: #d15c5c; }
.ground-truth h2 { color: #b97e00; font-size: 1.1em; margin-bottom: 4px; }
.ground-truth .gt-subtitle { color: #5e6a71; font-size: 0.85em; margin-bottom: 14px; }
.ground-truth .gt-verdict { padding: 10px 16px; border-radius: 8px; margin-bottom: 14px; font-weight: 600; font-size: 0.95em; }
.gt-verdict.cso-yes { background: #d15c5c; color: #fff; }
.gt-verdict.cso-no { background: #3c9160; color: #fff; }
.gt-verdict.no-data { background: #eef1f4; color: #5e6a71; border: 1px dashed #dfe5ea; }

.gt-day-tabs { display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; }
.gt-day-tab { padding: 6px 14px; border-radius: 6px; background: #eef1f4; color: #5e6a71; cursor: pointer; font-size: 0.8em; border: 1px solid #dfe5ea; transition: all 0.2s; }
.gt-day-tab:hover { border-color: #b97e00; }
.gt-day-tab.active { background: #e3eefb; color: #1f6fb0; border-color: #1f6fb0; }
.gt-day-tab .tab-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 4px; }

.gt-basin-group { margin-bottom: 12px; }
.gt-basin-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; padding-bottom: 4px; border-bottom: 1px solid #dfe5ea; }
.gt-basin-name { font-size: 0.9em; font-weight: 600; color: #26272a; }
.gt-basin-count { font-size: 0.75em; color: #5e6a71; }
.gt-basin-model { font-size: 0.75em; padding: 2px 8px; border-radius: 4px; font-weight: 600; }

.gt-station-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 6px; }
.gt-station { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 6px; background: #eef1f4; font-size: 0.82em; }
.gt-station-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.gt-station-dot.elevated { background: #d15c5c; }
.gt-station-dot.clean { background: #3c9160; }
.gt-station-name { flex: 1; color: #3a4a52; }
.gt-station-value { color: #8a949b; font-size: 0.9em; }
.gt-station-value.over { color: #d15c5c; font-weight: 600; }

.gt-no-samples { color: #8a949b; font-style: italic; font-size: 0.85em; padding: 10px; }

footer { text-align: center; padding: 20px; color: #8a949b; font-size: 0.8em; }
footer a { color: #2b7fbf; text-decoration: none; }
.refresh-info { font-size: 0.8em; color: #8a949b; text-align: center; margin-bottom: 15px; }
</style>
</head>
<body>
<div class="container">
<header>
    <h1><span class="live-dot"></span> SF CSO Live Forecast</h1>
    <div class="subtitle">Combined Sewer Overflow risk — updated every 30 minutes</div>
</header>

<div class="mode-bar">
    <button class="mode-btn active" id="btnLive" onclick="setMode('live')">📡 Live Forecast</button>
    <button class="mode-btn" id="btnHistorical" onclick="setMode('historical')">📅 Historical</button>
    <div id="datePickerWrap" style="display:none;">
        <input type="date" class="date-picker" id="datePicker" min="__MIN_DATE__" max="__MAX_DATE__" value="__DEFAULT_DATE__">
        <span class="historical-label" id="histLabel"></span>
    </div>
</div>

<div id="riskBanner" class="risk-banner risk-none">Loading...</div>
<div class="refresh-info" id="refreshInfo"></div>

<!-- 7-day timeline (yesterday + today + 5 forecast days) -->
<div class="timeline" id="timeline"></div>

<!-- Basin detail for today -->
<div class="basins" id="basins"></div>

<!-- Ground truth overlay (historical mode only) -->
<div id="groundTruth" class="ground-truth" style="display:none;">
    <h2>🔬 Ground Truth — Actual Bacteria Results</h2>
    <div class="gt-subtitle">SF Gov water quality samples compared to model predictions</div>
    <div id="gtVerdict" class="gt-verdict no-data"></div>
    <div class="gt-day-tabs" id="gtDayTabs"></div>
    <div id="gtContent"></div>
</div>

<!-- Current beach status -->
<div class="beach-section">
    <h2>🏖️ Current Beach Status (SFPUC Real-Time)</h2>
    <div class="beach-grid" id="beachGrid"></div>
</div>

<footer>
    <p>📞 1-877-SFBEACH (1-877-732-3224) or 415-242-2214</p>
    <p>⚠️ Avoid water contact during and 72 hours after rain</p>
    <p style="margin-top:8px;">
        <a href="https://webapps.sfpuc.org/sapps/beachesandbay.html">SFPUC Beach Map</a> ·
        Model: trained on SFPUC-reported discharge events 2016–2025 (holdout PR-AUC 0.87);
        shown risk = P(discharge) × measured beach-impact persistence by discharge size
    </p>
</footer>
</div>

<script>
function riskInfo(prob) {
    if (prob >= 0.75) return { label: 'EXTREME', color: '#d15c5c', bg: '#fbe0e0', cls: 'extreme' };
    if (prob >= 0.50) return { label: 'HIGH', color: '#c2521a', bg: '#fbe9dd', cls: 'high' };
    if (prob >= 0.25) return { label: 'MODERATE', color: '#b97e00', bg: '#fbf0d6', cls: 'moderate' };
    if (prob >= 0.10) return { label: 'LOW', color: '#6b8e23', bg: '#eef6dc', cls: 'low' };
    return { label: 'MINIMAL', color: '#3c9160', bg: '#e3f2ea', cls: 'none' };
}

const basinLabels = {
    citywide: { icon: '🏙️', name: 'City-wide', desc: 'Worst basin: sewage discharge impact' },
    westside: { icon: '🌊', name: 'Westside', desc: 'Ocean Beach, Baker Beach, China Beach' },
    north_shore: { icon: '🏖️', name: 'North Shore', desc: 'Crissy Field, Aquatic Park' },
    southeast: { icon: '⚓', name: 'Southeast', desc: 'Islais Creek, Candlestick Point' },
};

let currentMode = 'live';
let currentData = null;  // Store last rendered data for day selection

function setMode(mode) {
    currentMode = mode;
    document.getElementById('btnLive').className = 'mode-btn' + (mode === 'live' ? ' active' : '');
    document.getElementById('btnHistorical').className = 'mode-btn' + (mode === 'historical' ? ' active' : '');
    document.getElementById('datePickerWrap').style.display = mode === 'historical' ? 'inline-flex' : 'none';
    loadData();
}

document.getElementById('datePicker').addEventListener('change', () => { if (currentMode === 'historical') loadData(); });

async function loadData() {
    try {
        let data;
        if (currentMode === 'historical') {
            const date = document.getElementById('datePicker').value;
            if (!date) return;
            document.getElementById('histLabel').textContent = '⏳ Loading...';

            // Fetch predictions and bacteria data in parallel
            const [predResp, bacteriaResp] = await Promise.all([
                fetch('/api/historical?date=' + date),
                fetch('/api/bacteria?date=' + date),
            ]);
            const predData = await predResp.json();
            const bacteriaData = await bacteriaResp.json();

            if (predData.error) {
                document.getElementById('riskBanner').textContent = '⚠️ ' + predData.error;
                document.getElementById('histLabel').textContent = '❌ ' + predData.error;
                return;
            }
            // Wrap in same format as live data
            data = {
                predictions: predData.predictions,
                beach_status: [],
                historical_date: date,
                bacteria: bacteriaData,
            };
            document.getElementById('histLabel').textContent = '📅 Showing ' + date;
        } else {
            const resp = await fetch('/api/data');
            data = await resp.json();
        }
        render(data);
    } catch(e) {
        document.getElementById('riskBanner').textContent = '⚠️ Error loading data: ' + e.message;
    }
}

function render(data) {
    const preds = data.predictions || {};
    const beaches = data.beach_status || [];
    const isHistorical = !!data.historical_date;

    // Sort days
    const days = Object.values(preds).sort((a, b) => a.day_offset - b.day_offset);

    // Find today
    const today = days.find(d => d.is_today);
    const todayMax = today ? Math.max(...Object.values(today.predictions)) : 0;
    const overallMax = Math.max(...days.map(d => Math.max(...Object.values(d.predictions))));

    // Risk banner
    const banner = document.getElementById('riskBanner');
    const bannerRisk = riskInfo(todayMax);
    banner.className = 'risk-banner risk-' + bannerRisk.cls;
    const prefix = isHistorical ? '📅 ' + data.historical_date + ': ' : '';
    if (todayMax >= 0.50) {
        banner.innerHTML = prefix + '🚨 ' + bannerRisk.label + ' CSO RISK — Avoid water contact at affected beaches';
    } else if (todayMax >= 0.25) {
        banner.innerHTML = prefix + '⚠️ ' + bannerRisk.label + ' CSO RISK — Monitor conditions closely';
    } else if (todayMax >= 0.10) {
        banner.innerHTML = prefix + '🟡 ' + bannerRisk.label + ' CSO RISK — Some rain, worth watching';
    } else {
        banner.innerHTML = prefix + '✅ MINIMAL CSO RISK — Conditions favorable for beach recreation';
    }

    if (!isHistorical) {
        // Check if any forecast day is worse
        const futureDays = days.filter(d => d.is_forecast);
        if (futureDays.length > 0) {
            const futureMax = Math.max(...futureDays.map(d => Math.max(...Object.values(d.predictions))));
            if (futureMax > todayMax + 0.15 && futureMax >= 0.25) {
                const futureRisk = riskInfo(futureMax);
                banner.innerHTML += '<br><span style="font-size:0.8em;opacity:0.9;">⚠️ Higher risk coming: ' + futureRisk.label + ' (' + Math.round(futureMax*100) + '%) in forecast</span>';
            }
        }
    }

    // Refresh info
    const refreshEl = document.getElementById('refreshInfo');
    if (data.last_refresh) {
        const t = new Date(data.last_refresh);
        refreshEl.textContent = 'Last updated: ' + t.toLocaleTimeString() + ' · Auto-refreshes every 30 min';
    }

    // Store data for day selection
    currentData = { days, preds, isHistorical, data };

    // Timeline
    const rainSourceLabel = day => {
        const src = day.rain_source || (day.is_forecast ? 'forecast' : 'model');
        return {observed: '📊 SFO gauge', mixed: '📊 gauge + 📡 ECMWF',
                model: '📡 model hindcast', forecast: '📡 ECMWF forecast'}[src] || src;
    };
    const timeline = document.getElementById('timeline');
    timeline.innerHTML = '';
    days.forEach(day => {
        const maxProb = Math.max(...Object.values(day.predictions));
        const risk = riskInfo(maxProb);
        const pct = Math.round(maxProb * 100);

        const card = document.createElement('div');
        card.className = 'day-card' + (day.is_today ? ' today' : '');
        card.dataset.date = day.date;
        card.innerHTML = `
            <div class="day-label ${day.is_today ? 'today-label' : ''}">${day.label}</div>
            <div class="day-rain">☔ <span class="amount">${day.rain_inches.toFixed(2)}"</span></div>
            <div class="day-prob" style="color:${risk.color}">${pct}%</div>
            <div class="day-risk" style="color:${risk.color}">${risk.label}</div>
            <div class="day-bar"><div class="day-bar-fill" style="width:${pct}%;background:${risk.color}"></div></div>
            <div class="day-source">${rainSourceLabel(day)}</div>
        `;
        card.onclick = () => selectDay(day);
        timeline.appendChild(card);
    });

    // Show basin detail for "today" by default
    selectDay(today || days[0]);

    // Beach status
    const beachGrid = document.getElementById('beachGrid');
    beachGrid.innerHTML = '';
    if (isHistorical) {
        beachGrid.innerHTML = '<div style="color:#8a949b;">Real-time beach status not available for historical dates — see Ground Truth section below for actual bacteria results</div>';
    } else if (beaches.length === 0) {
        beachGrid.innerHTML = '<div style="color:#8a949b;">Unable to fetch beach status</div>';
    }
    // Sort: CSO first, then posted, then safe
    const order = { cso: 0, posted: 1, safe: 2, not_sampled: 3, unknown: 4 };
    beaches.sort((a, b) => (order[a.status] || 9) - (order[b.status] || 9));
    beaches.forEach(b => {
        const item = document.createElement('div');
        item.className = 'beach-item';
        const statusLabel = b.status === 'cso' ? '🚨 CSO' : b.status === 'posted' ? '⚠️ Posted' : b.status === 'safe' ? '✅ Safe' : '⚪ No data';
        item.innerHTML = `<span class="beach-dot ${b.status}"></span><span>${b.name}</span><span style="margin-left:auto;font-size:0.75em;color:#8a949b;">${statusLabel}</span>`;
        beachGrid.appendChild(item);
    });

    // Ground truth overlay (historical mode only)
    renderGroundTruth(data, preds);
}

function selectDay(day) {
    if (!day || !currentData) return;

    // Update timeline selection highlight
    document.querySelectorAll('.day-card').forEach(c => {
        c.classList.remove('selected');
        if (c.dataset.date === day.date) c.classList.add('selected');
    });

    // Update basin detail for selected day
    const basinsEl = document.getElementById('basins');
    basinsEl.innerHTML = '';
    for (const [key, prob] of Object.entries(day.predictions)) {
        const info = basinLabels[key] || { icon: '📍', name: key, desc: '' };
        const risk = riskInfo(prob);
        const pct = Math.round(prob * 100);

        const card = document.createElement('div');
        card.className = 'basin-card';
        card.style.borderLeftColor = risk.color;
        card.innerHTML = `
            <div class="basin-name">${info.icon} ${info.name}</div>
            <div class="basin-prob" style="color:${risk.color}">${pct}%</div>
            <div class="basin-bar"><div class="basin-bar-fill" style="width:${pct}%;background:${risk.color}"></div></div>
            <div class="basin-detail">${info.desc} · 2d rain: ${day.rain_2d_cum.toFixed(2)}" · 3d: ${day.rain_3d_cum.toFixed(2)}"</div>
        `;
        basinsEl.appendChild(card);
    }

    // Update ground truth to show the selected day's bacteria data
    if (currentData.isHistorical && currentData.data.bacteria && currentData.data.bacteria.has_data) {
        const bDays = currentData.data.bacteria.days;
        if (bDays[day.date]) {
            // Auto-click the matching ground truth tab
            document.querySelectorAll('.gt-day-tab').forEach(tab => {
                if (tab.textContent.includes(day.date)) tab.click();
            });
        }
    }
}

// Basin key mapping for matching predictions to ground truth
const basinKeyMap = { 'Westside': 'westside', 'North Shore': 'north_shore', 'Southeast': 'southeast' };

function renderGroundTruth(data, preds) {
    const gtEl = document.getElementById('groundTruth');
    const gtVerdict = document.getElementById('gtVerdict');
    const gtTabs = document.getElementById('gtDayTabs');
    const gtContent = document.getElementById('gtContent');

    if (!data.bacteria || !data.bacteria.has_data || !data.historical_date) {
        gtEl.style.display = data.historical_date ? 'block' : 'none';
        if (data.historical_date) {
            gtEl.className = 'ground-truth';
            gtVerdict.className = 'gt-verdict no-data';
            gtVerdict.textContent = '📭 No bacteria samples found for this date range — SFPUC samples weekly, so data may not be available for every day.';
            gtTabs.innerHTML = '';
            gtContent.innerHTML = '';
        }
        return;
    }

    gtEl.style.display = 'block';
    const bacteria = data.bacteria;
    const bDays = bacteria.days;
    const sortedDates = Object.keys(bDays).sort();

    // Find the target date's data (or closest)
    const targetDate = data.historical_date;
    let hasCso = false;
    for (const d of sortedDates) {
        if (bDays[d].summary.likely_cso) hasCso = true;
    }
    gtEl.className = 'ground-truth' + (hasCso ? ' has-cso' : '');

    // Verdict banner
    const targetDay = bDays[targetDate];
    if (targetDay) {
        const s = targetDay.summary;
        if (s.likely_cso) {
            gtVerdict.className = 'gt-verdict cso-yes';
            gtVerdict.innerHTML = '🚨 CSO EVENT CONFIRMED — ' + s.elevated_stations + '/' + s.total_stations +
                ' stations elevated across ' + s.elevated_basins + ' basin(s) · ' +
                s.elevated_samples + '/' + s.total_samples + ' samples exceeded standards';
        } else if (s.elevated_stations > 0) {
            gtVerdict.className = 'gt-verdict cso-no';
            gtVerdict.innerHTML = '⚠️ Some elevated readings — ' + s.elevated_stations + '/' + s.total_stations +
                ' stations elevated, but not enough for CSO classification (' + s.elevated_basins + ' basin)';
        } else {
            gtVerdict.className = 'gt-verdict cso-no';
            gtVerdict.innerHTML = '✅ All clear — 0/' + s.total_stations + ' stations exceeded standards';
        }
    } else {
        // No samples on exact target date, show closest
        const closestDate = sortedDates.length > 0 ? sortedDates[0] : null;
        if (closestDate) {
            const s = bDays[closestDate].summary;
            const label = s.likely_cso ? '🚨 CSO EVENT' : s.elevated_stations > 0 ? '⚠️ Elevated' : '✅ Clear';
            gtVerdict.className = 'gt-verdict' + (s.likely_cso ? ' cso-yes' : ' cso-no');
            gtVerdict.innerHTML = label + ' on nearest sample date (' + closestDate + ') — ' +
                s.elevated_stations + '/' + s.total_stations + ' stations elevated · No samples on ' + targetDate;
        } else {
            gtVerdict.className = 'gt-verdict no-data';
            gtVerdict.textContent = '📭 No samples found near ' + targetDate;
        }
    }

    // Day tabs
    gtTabs.innerHTML = '';
    let activeDate = sortedDates.includes(targetDate) ? targetDate : (sortedDates[0] || null);
    sortedDates.forEach(d => {
        const s = bDays[d].summary;
        const dotColor = s.likely_cso ? '#d15c5c' : s.elevated_stations > 0 ? '#b97e00' : '#3c9160';
        const isTarget = d === targetDate;
        const tab = document.createElement('div');
        tab.className = 'gt-day-tab' + (d === activeDate ? ' active' : '');
        tab.innerHTML = '<span class="tab-dot" style="background:' + dotColor + '"></span>' +
            d + (isTarget ? ' ★' : '') +
            ' <span style="color:#8a949b;">(' + s.elevated_stations + '/' + s.total_stations + ')</span>';
        tab.onclick = () => {
            document.querySelectorAll('.gt-day-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            renderGroundTruthDay(bDays[d], d, preds);
        };
        gtTabs.appendChild(tab);
    });

    // Render the active day
    if (activeDate) {
        renderGroundTruthDay(bDays[activeDate], activeDate, preds);
    }
}

function renderGroundTruthDay(dayData, dateStr, preds) {
    const gtContent = document.getElementById('gtContent');
    const summary = dayData.summary;
    const basins = summary.basins;

    // Get model prediction for this date if available
    const dayPred = preds[dateStr];

    let html = '';

    // Order basins: Westside, North Shore, Southeast
    const basinOrder = ['Westside', 'North Shore', 'Southeast'];
    for (const basinName of basinOrder) {
        const basin = basins[basinName];
        if (!basin) continue;

        const basinKey = basinKeyMap[basinName];
        const modelProb = dayPred ? dayPred.predictions[basinKey] : null;

        html += '<div class="gt-basin-group">';
        html += '<div class="gt-basin-header">';
        html += '<span class="gt-basin-name">' + basinName + '</span>';
        html += '<span class="gt-basin-count">' + basin.elevated + '/' + basin.total + ' stations elevated</span>';

        // Model prediction badge
        if (modelProb !== null && modelProb !== undefined) {
            const pct = Math.round(modelProb * 100);
            const risk = riskInfo(modelProb);
            const actual = basin.elevated > 0;
            const predicted = modelProb >= 0.10;
            const match = actual === predicted;
            html += '<span class="gt-basin-model" style="background:' + risk.bg + ';color:' + risk.color + ';">' +
                'Model: ' + pct + '%</span>';
            html += '<span style="font-size:0.75em;">' + (match ? '✅' : '❌') + '</span>';
        }

        html += '</div>';

        // Station grid
        html += '<div class="gt-station-grid">';
        // Sort: elevated first
        const stations = basin.stations.sort((a, b) => (b.elevated ? 1 : 0) - (a.elevated ? 1 : 0));
        for (const st of stations) {
            const dotClass = st.elevated ? 'elevated' : 'clean';
            const enteroStr = st.max_entero !== null ?
                (st.max_entero > 104 ?
                    '<span class="gt-station-value over">Entero: ' + Math.round(st.max_entero) + '</span>' :
                    '<span class="gt-station-value">Entero: ' + Math.round(st.max_entero) + '</span>') :
                '<span class="gt-station-value">—</span>';
            const countStr = '<span class="gt-station-value">' + st.exceedance_count + '/' + st.sample_count + '</span>';

            html += '<div class="gt-station">' +
                '<span class="gt-station-dot ' + dotClass + '"></span>' +
                '<span class="gt-station-name">' + st.name + '</span>' +
                enteroStr + countStr +
                '</div>';
        }
        html += '</div></div>';
    }

    gtContent.innerHTML = html;
}

// Initial load
loadData();

// Auto-refresh every 5 minutes (data itself refreshes every 30 min server-side)
setInterval(loadData, 300000);
</script>
</body>
</html>"""


def main():
    # Initial data fetch
    print("Fetching initial data...")
    LIVE.refresh()

    # Start background refresh thread
    thread = Thread(target=refresh_loop, args=(LIVE, 1800), daemon=True)
    thread.start()

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  SF CSO Live Forecast Dashboard                              ║
╠══════════════════════════════════════════════════════════════╣
║  🌐 Open: http://localhost:{PORT}                               ║
║  📡 Data: Open-Meteo (ECMWF) + SFPUC real-time               ║
║  🔄 Auto-refreshes every 30 minutes                          ║
║  Press Ctrl+C to stop                                        ║
╚══════════════════════════════════════════════════════════════╝
""")

    with socketserver.TCPServer(("", PORT), LiveDashboardHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 Shutting down...")


if __name__ == "__main__":
    main()
