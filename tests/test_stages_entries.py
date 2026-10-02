"""The entries (P7b, 2026-10-01): the input frames S2 is fed, per entry of protocol §3.

What is pinned, and why:
- rain known is the training frame: frames('rain') equals train_v4.build_dataset(..., input_rules=
  ['gauge_outage_v1']) column for column, dtype for dtype, on every shared day (and the raw record's,
  with no rules), so the S2 oracle is fed exactly what the weights were trained on;
- every entry carries build_dataset's 19 feature columns, in its order and dtypes, with no NaN, and every
  rain source holds the same days;
- lead L is the composite series (gauges to I − 1 as the issue day knew them, then day d at lead
  d − I): it matches the shared feature code run over the whole composite record, and with a perfect
  forecast it is the rain entry (bar the issue days the whole-record outage mask reads with hindsight);
- the gauges as the issue day knew them are train_v4.rain_series itself, run on the record cut at I − 1:
  the gauge-days the whole-record mask hides that the cut record does not are listed exactly, as of
  2026-08-17, and the lead entries read them as filed (protocol §3: nothing unknown at issue time);
- as served is the live page's computation: L0s / L1s equal LiveData._daily_frames run offline on the
  same gauges and forecast hours, and differ from L0 / L1 in the four history features only;
  history_full_v1 differs from lead L only where a dry spell outlasts its 35-day frame;
- a missing forecast hour is never a dry hour: a short model-day, or a peak window with a NaN hour, drops
  the row with X-S1-NWPGAP, a day the archive does not reach with X-S1-NOLEAD;
- the entries' availability, as of the committed data's end (2026-08-17).

Run: venv/bin/python tests/test_stages_entries.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FORECAST = ROOT / "features" / "forecast"
for p in (str(ROOT), str(FORECAST), str(FORECAST / "src" / "models")):
    if p not in sys.path:
        sys.path.insert(0, p)

import exclusions as X  # noqa: E402
import leaderboard  # noqa: E402
import rain_features as RF  # noqa: E402
import stages_entries as E  # noqa: E402
import train_v4 as T4  # noqa: E402
import truth as T  # noqa: E402

AS_OF = "2026-08-17"
SOURCES = list(T4.RAIN_SOURCES)                       # avg, SF Oceanside, SF Downtown
DAY = pd.Timedelta(days=1)
RULES = ("gauge_outage_v1",)
CUT_DAYS = 400       # an issue day's record is cut to its last 400 days (every outage run is far shorter) …
CHECK_DAYS = 250     # … and read on the last 250, which no run the cut truncates can reach
_cache = {}


def _late():
    """The issue days whose gauges the whole-record mask reads with hindsight (checked in
    test_lead_entries_read_the_gauges_as_the_issue_day_knew_them)."""
    return set(E.issue_time_unmasked()["issue"])


def _record_through(I):
    """{source: train_v4.rain_series} run on historical_rain.csv cut to I − 400 … I − 1: the gauges as issue
    day I knew them, from the served-path code itself (read on the last CHECK_DAYS days only)."""
    key = ("through", I)
    if key not in _cache:
        if "csv" not in _cache:
            _cache["csv"] = pd.read_csv(T.RAIN_CSV, parse_dates=["date"])
        rain = _cache["csv"]
        cut = rain[(rain["date"] >= I - CUT_DAYS * DAY) & (rain["date"] <= I - DAY)]
        with tempfile.TemporaryDirectory() as tmp:
            cut.to_csv(Path(tmp) / "historical_rain.csv", index=False)
            with patch.object(T4, "RAW_DIR", Path(tmp)):
                _cache[key] = {s: T4.rain_series(s, list(RULES))[0].loc[I - CHECK_DAYS * DAY:] for s in SOURCES}
    return _cache[key]


def _history(source, I):
    """The gauge record through I − 1 as issue day I knew it, for the reference composites."""
    whole = E._gauge(source, RULES).loc[:I - DAY]
    if I not in _late():
        return whole
    return pd.concat([whole.loc[:I - (CHECK_DAYS + 1) * DAY], _record_through(I)[source]])


def _dataset(rules=("gauge_outage_v1",)):
    key = ("bd", rules)
    if key not in _cache:
        _cache[key] = T4.build_dataset(end=E.data_end(), sources=SOURCES, input_rules=list(rules) or None)[0]
    return _cache[key]


def _frames(entry, sources=("avg", "SF Downtown"), model="icon_seamless", **kw):
    key = (entry, tuple(sources), model, tuple(sorted(kw.items())))
    if key not in _cache:
        _cache[key] = E.frames(entry, list(sources), model=model, **kw)
    return _cache[key]


# ── rain known ──────────────────────────────────────────────────────────────

def test_rain_entry_is_the_training_frame():
    assert E.data_end() == pd.Timestamp(AS_OF), E.data_end()          # ERA5 and both gauges, as of the freeze
    bd = _dataset()
    fr = E.frames("rain", SOURCES)
    oracle = E.frames("oracle", SOURCES)
    for s in SOURCES:
        want = bd[s][["date"] + E.FEATURES].reset_index(drop=True)
        pd.testing.assert_frame_equal(fr[s], want, check_exact=True)   # values, names, order, dtypes
        pd.testing.assert_frame_equal(oracle[s], fr[s], check_exact=True)
        assert fr[s].attrs["input_rules"] == ["gauge_outage_v1"] and fr[s].attrs["dropped"] == {}
    raw = E.frames("rain", SOURCES, input_rules=())                   # the record the served weights were fit on
    bd_raw = _dataset(())
    for s in SOURCES:
        pd.testing.assert_frame_equal(raw[s], bd_raw[s][["date"] + E.FEATURES].reset_index(drop=True), check_exact=True)
    assert not raw["avg"].equals(fr["avg"]), "the outage rule must change the rain entry somewhere"
    part = E.frames("rain", ["avg"], start="2024-01-01", end="2024-03-31")["avg"]
    full = fr["avg"].set_index("date").loc["2024-01-01":"2024-03-31"].reset_index()
    pd.testing.assert_frame_equal(part, full, check_exact=True)


def test_every_entry_carries_the_training_columns():
    want = _dataset()["avg"][["date"] + E.FEATURES].dtypes
    assert E.FEATURES == list(leaderboard.FEATS), "the 19 inputs the served weights read"
    for e in E.ENTRIES:
        fr = _frames(e)
        days = None
        for s, f in fr.items():
            assert list(f.columns) == ["date"] + E.FEATURES, (e, s)
            assert (f.dtypes == want).all(), (e, s, f.dtypes[f.dtypes != want])
            assert not f[E.FEATURES].isna().any().any(), (e, s)
            assert f["date"].is_monotonic_increasing and not f["date"].duplicated().any(), (e, s)
            assert isinstance(f.index, pd.RangeIndex) and f.index[0] == 0, (e, s)
            assert f.attrs["entry"] == e and f.attrs["source"] == s
            assert f.attrs["lead_kind"] == {None: None, 0: "short_lead_optimistic"}.get(E.lead_of(e), "fixed_lead"), (e, f.attrs)
            days = f["date"] if days is None else days
            assert f["date"].equals(days), (e, "every source holds the same days")
        assert len(days), e


# ── lead L ──────────────────────────────────────────────────────────────────

def test_lead_entries_are_the_composite_series():
    """Each sampled row equals the shared feature code run over the whole composite record (gauges through
    I − 1, then each day d at lead d − I): integers exactly, sums to rounding; peaks from the lead-L hours."""
    late = _late()
    for L in (0, 1, 3, 5):
        f = _frames(f"L{L}")["avg"].set_index("date")
        pick = f.index[np.random.default_rng(L).choice(len(f), 40, replace=False)]
        hind = f.index[(f.index - L * DAY).isin(list(late))]
        assert len(hind) >= 4, (L, "the rows the hindsight mask would change are sampled too")
        pick = pick.union(hind)
        pk = E.model_peaks("icon_seamless", L).set_index("date")
        for D in pick:
            I = D - L * DAY
            fc = pd.Series([E.model_days("icon_seamless", j).set_index("date").at[I + j * DAY, "total"] for j in range(L + 1)],
                           index=pd.date_range(I, D))
            comp = pd.concat([_history("avg", I), fc])
            ref = RF.add_daily_features(pd.DataFrame({"precip_inches": comp.to_numpy()})).iloc[-1]
            for c in RF.DAILY_FEATURES:
                if c in E.INT_FEATURES:
                    assert int(ref[c]) == int(f.at[D, c]), (L, D.date(), c, ref[c], f.at[D, c])
                else:
                    assert abs(float(ref[c]) - float(f.at[D, c])) < 1e-12, (L, D.date(), c, ref[c], f.at[D, c])
            assert pk.loc[D, "complete"]
            for c in RF.INTENSITY_FEATURES:
                assert f.at[D, c] == pk.at[D, c], (L, D.date(), c)
            assert f.at[D, "precip_avg"] == fc.iloc[-1] and (L == 0 or f.at[D, "rain_lag1d"] == fc.iloc[-2])


def test_a_perfect_forecast_gives_the_rain_entry():
    """Feed the gauges in place of the forecast: every lead entry, as-served history aside, is then the rain
    entry — the composite, the 35-day frame and the dry-spell carry across its start included — except on
    the issue days whose record through I − 1 has not yet called an outage the whole record masks."""
    rain = _frames("rain", ("avg",))["avg"].set_index("date")
    late = _late()
    g = E._gauge("avg", ("gauge_outage_v1",))
    leads = (0, 1, 2, 5)
    rows = {L: pd.DatetimeIndex(_frames(f"L{L}", ("avg",))["avg"]["date"]) for L in leads}   # built before the swap
    real_total = E._total
    E._lead_tables.cache_clear()
    try:
        E._total = lambda model, lead, dates: g.reindex(pd.DatetimeIndex(dates)).to_numpy(dtype=float)
        for L in leads:
            days = rows[L]
            on_time = ~(days - L * DAY).isin(list(late))
            assert (~on_time).sum() >= 4, L
            days = days[on_time]
            vals = E._lead_values("icon_seamless", "avg", ("gauge_outage_v1",), L, False, "served", days)
            for i, c in enumerate(RF.DAILY_FEATURES):
                ref = rain.loc[days, c].to_numpy(dtype=float)
                if c in E.INT_FEATURES:
                    assert np.array_equal(vals[:, i], ref), (L, c, np.flatnonzero(vals[:, i] != ref)[:5])
                else:
                    assert np.abs(vals[:, i] - ref).max() < 1e-12, (L, c)
            long_dry = rain.loc[days, "dry_spell_days"] > E.FULL_PAST_DAYS + L + 1
            assert long_dry.sum() > 50, (L, "summer dry spells outlast the frame, so the carry is exercised")
    finally:
        E._total = real_total
        E._lead_tables.cache_clear()


# ── the gauges as the issue day knew them ───────────────────────────────────

HINDSIGHT_AS_OF = {"issue_days": 118, "gauge_days": 1013, "since_2022_11_17": 64, "since_2024_01_20": 19}   # 2026-08-17


def test_lead_entries_read_the_gauges_as_the_issue_day_knew_them():
    """The gauge_outage_v1 mask run on the record cut at I − 1 against the whole record's: the gauge-days the
    whole record masks and the cut record does not are exactly issue_time_unmasked's (the cut never masks
    more), on every issue day an outage run reaches and on a sample of others. On the listed issue days the
    lead entries' gauges are train_v4.rain_series itself run on the cut record; elsewhere the whole record."""
    raw, _, status, _ = T._gauge_record()
    runs = T.outage_runs()
    assert max(r["days"] for r in runs) < CUT_DAYS - CHECK_DAYS, "a run longer than the cut can see"
    cells = E.issue_time_unmasked()
    assert list(cells.columns) == ["issue", "date", "gauge"] and E.issue_time_unmasked(input_rules=()).empty
    got = set(zip(cells["issue"], cells["date"], cells["gauge"]))
    reached = pd.DatetimeIndex(sorted({I for r in runs for I in pd.date_range(pd.Timestamp(r["start"]) + DAY, pd.Timestamp(r["end"]) + DAY)}))
    others = pd.date_range("2017-01-01", AS_OF).difference(reached)
    issues = reached.union(others[np.random.default_rng(0).choice(len(others), 40, replace=False)])
    want = set()
    for I in issues:
        cut = raw.loc[I - CUT_DAYS * DAY:I - DAY]
        then = RF.mask_gauge_outages(cut.reset_index(), gauges=T.GAUGE_SERIES)[0].set_index("date")[list(T.GAUGE_SERIES)]
        zone = cut.index[cut.index >= I - CHECK_DAYS * DAY]
        was = then.loc[zone].isna() & cut.loc[zone].notna()            # masked as of I
        is_ = status.loc[zone, list(T.GAUGE_SERIES)] == "outage"         # masked on the whole record
        assert not (was & ~is_).to_numpy().any(), (I.date(), "the cut record masks a day the whole record does not")
        for g in T.GAUGE_SERIES:
            want |= {(I, d, g) for d in zone[(is_[g] & ~was[g]).to_numpy()]}
    assert got == want, (sorted(got - want)[:3], sorted(want - got)[:3])
    iss = pd.DatetimeIndex(sorted(set(cells["issue"])))
    assert {"issue_days": len(iss), "gauge_days": len(cells), "since_2022_11_17": int((iss >= "2022-11-17").sum()),
            "since_2024_01_20": int((iss >= "2024-01-20").sum())} == HINDSIGHT_AS_OF
    assert pd.Timestamp("2026-02-05") in iss                           # the outage the rule was built from, before it could be called
    # the series: train_v4.rain_series on the cut record, every source, on the listed issue days the lead tables hold
    for I in iss[iss >= "2022-11-17"]:
        ref = _record_through(I)
        for s in SOURCES:
            mine = E._issue_records(s, RULES)[I][0].loc[ref[s].index]
            assert np.array_equal(mine.to_numpy(), ref[s].to_numpy()), (I.date(), s)
    for I in others[np.random.default_rng(1).choice(len(others), 5, replace=False)]:
        ref = _record_through(I)
        for s in SOURCES:
            assert np.array_equal(E._gauge(s, RULES).loc[ref[s].index].to_numpy(), ref[s].to_numpy()), (I.date(), s)
    # and the entries use it: L1 reads the record through I − 1 on those issue days
    for s, frame in _frames("L1").items():
        frame = frame.set_index("date")
        for I in iss:
            D = I + DAY
            if D in frame.index:
                h = _history(s, I)
                # D − 2 = I − 1 is the last gauge day, D − 7 = I − 6 (D − 1 = I is the lead-0 forecast)
                assert abs(frame.at[D, "rain_lag2d"] - h.iloc[-1]) < 1e-12 and abs(frame.at[D, "rain_lag7d"] - h.iloc[-6]) < 1e-12, (s, D.date())


# ── as served ───────────────────────────────────────────────────────────────

class _Acis:
    def __init__(self, js):
        self._js = js

    def raise_for_status(self):
        pass

    def json(self):
        return self._js


def test_as_served_is_what_the_live_page_computes():
    """LiveData._daily_frames, run offline: ACIS answers with the gauge record as the issue day knew it,
    outage-masked on the record through I − 1 (the inputs every lead entry shares), the hourly series holds
    each forecast day's archived hours at its lead. The daily features it makes for today and tomorrow are
    L0s and L1s, bit for bit, on issue days the hindsight mask would change too. The page's window is the
    one the entries use."""
    from features.forecast import live_dashboard as ld
    assert E.SERVED_PAST_DAYS == ld.METEO_PARAMS["past_days"] == 7 and E.FC_DAYS == ld.METEO_PARAMS["forecast_days"] == 6
    raw = T._gauge_record()[0]
    gauge_of = {sid: name for name, sid in ld.ACIS_GAUGES.items()}
    now = {}

    def masked_through(I):
        cut = raw.loc[I - CUT_DAYS * DAY:I - DAY]
        m = RF.mask_gauge_outages(cut.reset_index(), gauges=T.GAUGE_SERIES)[0].set_index("date")[list(T.GAUGE_SERIES)]
        return m.mask(m == T.TRACE_IN, 0.0)

    def post(url, json=None, timeout=None, **kw):  # noqa: A002
        assert url == ld.ACIS_URL, url
        assert pd.Timestamp(json["edate"]) < now["I"], "the page asked ACIS for a day not yet past"
        s = now["rec"][gauge_of[json["sid"]]]
        return _Acis({"data": [[str(d.date()), "M" if pd.isna(s.get(d)) else f"{float(s.get(d)):.2f}"]
                               for d in pd.date_range(json["sdate"], json["edate"])]})

    def no_get(*a, **kw):
        raise AssertionError(f"network GET {a[:1]}")

    model = "icon_seamless"
    served = {e: _frames(e, model=model) for e in ("L0s", "L1s")}
    hourly = {j: E._hourly(model, j)[0] for j in range(E.FC_DAYS)}
    issues = pd.DatetimeIndex(served["L1s"]["avg"]["date"].iloc[::23] - DAY)
    late = pd.DatetimeIndex(sorted(_late()))
    late = late[late >= served["L0s"]["avg"]["date"].min()]
    assert len(late) >= 60, len(late)
    issues = issues.union(late)
    checked = 0
    with patch.object(ld.requests, "post", post), patch.object(ld.requests, "get", no_get), patch.object(ld, "_supabase", None):
        eng = ld.LiveData()
        for I in issues:
            now["I"], now["rec"] = I, masked_through(I)
            hrs = pd.date_range(I - E.SERVED_PAST_DAYS * DAY, I + E.FC_DAYS * DAY - pd.Timedelta(hours=1), freq="h")
            vals = np.zeros(len(hrs))                  # past hours: re-based onto the gauges by the page itself
            for j in range(E.FC_DAYS):
                m = (hrs >= I + j * DAY) & (hrs < I + (j + 1) * DAY)
                vals[m] = hourly[j].reindex(hrs[m]).to_numpy()
            frames = eng._daily_frames(pd.DataFrame({"timestamp": hrs, "precip_mm": vals * 25.4, "precip_inches": vals}), I.date())
            for e, L in (("L0s", 0), ("L1s", 1)):
                D = I + L * DAY
                for src, mine in served[e].items():
                    mine = mine.set_index("date")
                    if D not in mine.index:
                        continue
                    live = frames[src].set_index("date").loc[D]
                    for c in RF.DAILY_FEATURES:
                        assert float(live[c]) == float(mine.at[D, c]), (e, src, D.date(), c, live[c], mine.at[D, c])
                        checked += 1
    assert checked > 5000, checked


def test_as_served_differs_from_lead_l_only_in_history():
    for L in (0, 1):
        lead, srv = _frames(f"L{L}"), _frames(f"L{L}s")
        full = _frames(f"L{L}s", history="full")
        for s in lead:
            a, b, c = lead[s], srv[s], full[s]
            assert a["date"].equals(b["date"]) and a["date"].equals(c["date"]), (L, s)
            for col in E.FEATURES:
                same = np.array_equal(a[col].to_numpy(), b[col].to_numpy())
                if col in E.HISTORY_FEATURES:
                    assert not same, (L, s, col, "seven past days must truncate it somewhere")
                    assert np.array_equal(a[col].to_numpy(), c[col].to_numpy()) == (col != "dry_spell_days"), (L, s, col)
                else:
                    assert same and np.array_equal(a[col].to_numpy(), c[col].to_numpy()), (L, s, col)
            # a dry spell reads capped at the frame: 7 past days + L + 1 as served, 35 + L + 1 under history_full_v1
            dry = a["dry_spell_days"].to_numpy()
            assert np.array_equal(b["dry_spell_days"].to_numpy(), np.minimum(dry, E.SERVED_PAST_DAYS + L + 1)), (L, s)
            assert np.array_equal(c["dry_spell_days"].to_numpy(), np.minimum(dry, E.FULL_PAST_DAYS + L + 1)), (L, s)
            assert b.attrs["history"] == "served" and c.attrs["history"] == "full" and a.attrs["history"] is None


# ── never a missing hour as a dry one ───────────────────────────────────────

def test_a_short_model_day_drops_the_row_never_zero():
    days = E.model_days("ecmwf_ifs025", 0).set_index("date")
    gap = days.index[(~days["complete"]) & (days.index >= "2024-02-03") & (days.index <= "2024-02-29")]
    assert len(gap) == 11 and (days.loc[gap, "n_hours"] < 24).all() and days.loc[gap, "total"].isna().all()   # the Feb 2024 gap
    l0 = set(_frames("L0", ("avg",), model="ecmwf_ifs025")["avg"]["date"])
    assert not l0 & set(gap)
    dr = E.drops("L0", "ecmwf_ifs025").set_index("date")
    assert (dr.loc[gap, "reason"] == E.NWPGAP).all() and (dr.loc[gap, "lead"] == 0).all() and (dr.loc[gap, "what"] == "daily total").all()
    # a lead-5 row needs I … I + 5: each gap day takes out the five rows that forecast from it
    d5 = E.drops("L5", "ecmwf_ifs025").set_index("date")
    assert set(gap + 5 * DAY) <= set(d5.index[d5["reason"] == E.NWPGAP])
    # a peak window that reaches a NaN hour drops the row too (ICON's lead-1 hours of 2026-04-12 are short)
    d1 = E.drops("L1", "icon_seamless").set_index("date")
    assert d1.loc["2026-04-13", "reason"] == E.NWPGAP and d1.loc["2026-04-13", "what"] == "peak hours"
    assert E.model_days("icon_seamless", 1).set_index("date").loc["2026-04-13", "complete"]
    # the hour grid never invents a dry hour, and every kept row's inputs are archived
    for m in E.MODELS:
        for L in E.LEADS:
            s, inside = E._hourly(m, L)
            assert s[~inside.to_numpy()].isna().all(), (m, L)
    for e in ("L0", "L1", "L5", "L1s"):
        f = _frames(e, ("avg",), model="ecmwf_ifs025")["avg"]
        L = E.lead_of(e)
        for j in range(L + 1):
            ok = E.model_days("ecmwf_ifs025", j).set_index("date")["complete"]
            assert ok.reindex(pd.DatetimeIndex(f["date"]) - (L - j) * DAY).fillna(False).all(), (e, j)


def test_a_day_no_gauge_read_raises_never_reads_dry():
    """rain_series would fill a day neither gauge read with 0: inside the span that must raise (none does today)."""
    real = T.gauges

    def one_unread(start=None, end=None):
        g = real(start, end)
        g.loc[g["date"] == pd.Timestamp("2025-02-04"), ["raw", "status"]] = [np.nan, "missing"]
        return g

    caches = (E._gauge, E._lead_tables, E._per_gauge, E._hindsight, E._issue_records)
    for fn in caches:
        fn.cache_clear()
    try:
        with patch.object(E.T, "gauges", one_unread):
            _raises(ValueError, E._gauge, "avg", ("gauge_outage_v1",))
            _raises(ValueError, E._gauge, "SF Downtown", ())
            _raises(ValueError, E.frames, "rain", ["avg"], start="2025-01-01", end="2025-03-01")
    finally:
        for fn in caches:
            fn.cache_clear()
    E._gauge("avg", ("gauge_outage_v1",))                              # and the real record passes


def test_drops_and_rows_partition_the_span():
    days = pd.date_range(E.START, E.data_end())
    for m in E.MODELS:
        for e in E.ENTRIES:
            f = _frames(e, ("avg",), model=m)["avg"]
            dr = E.drops(e, m)
            assert len(f) + len(dr) == len(days), (m, e)
            assert not set(f["date"]) & set(dr["date"]), (m, e)
            assert set(dr["reason"]) <= {E.NWPGAP, E.NOLEAD}, (m, e)
            runs = f.attrs["dropped"]
            listed = sum((pd.Timestamp(b) - pd.Timestamp(a)).days + 1 for r in runs.values() for a, b in r)
            assert listed == len(dr), (m, e)
            if E.lead_of(e) is None:
                assert not len(dr) and f.attrs["model"] is None, (m, e)


def test_the_archive_hours_feed_the_s1_rules():
    """nwp_hours is exclusions.Context.nwp's input: a day S1 calls NWPGAP at lead 0 is a day L0 drops."""
    ctx = X.context("sfpuc4_v1", start="2024-01-20", end=AS_OF)
    for m in E.MODELS:
        nwp = E.nwp_hours(m)
        assert list(nwp.columns) == ["date", "lead", "n_hours"] and set(nwp["lead"]) == set(E.LEADS)
        c = ctx.with_inputs(nwp=nwp)
        sk = X.skeleton("S1", ["SF Downtown"], "2024-01-20", AS_OF, entry="L0", tier="T1")
        g = T.gauge_rain("2024-01-20", AS_OF)["SF Downtown"]
        sk["y"] = g.to_numpy()
        sk["p"] = E.model_days(m, 0).set_index("date")["total"].reindex(g.index).to_numpy()
        r = X.apply(sk, "S1", c)
        gap = set(r.loc[r["excl"] == E.NWPGAP, "date"])
        dr = E.drops("L0", m, start="2024-01-20", end=AS_OF)
        assert gap <= set(dr["date"]), (m, sorted(gap - set(dr["date"]))[:5])


# ── availability, as of the committed data's end ────────────────────────────

SPANS_AS_OF = {   # entry: (first day, rows 2016-03-01 → 2026-08-17)
    "icon_seamless": {"rain": ("2016-03-01", 3822), "L0": ("2022-11-17", 1370), "L1": ("2024-01-20", 936), "L2": ("2024-01-21", 935),
                      "L3": ("2024-01-22", 934), "L4": ("2024-01-23", 933), "L5": ("2024-01-24", 932)},
    "ecmwf_ifs025": {"L0": ("2024-02-03", 916), "L1": ("2024-02-04", 915), "L2": ("2024-02-05", 914), "L3": ("2024-02-06", 913),
                     "L4": ("2024-02-07", 912), "L5": ("2024-02-08", 905)},
    "gfs_seamless": {"L0": ("2024-02-01", 929), "L1": ("2024-02-01", 929), "L2": ("2024-02-02", 928), "L3": ("2024-02-03", 927),
                     "L4": ("2024-02-04", 926), "L5": ("2024-02-05", 925)},
}


def test_lead_availability_as_of_2026_08_17():
    for m, want in SPANS_AS_OF.items():
        sp = E.spans(m, end=AS_OF)
        for e, (first, n) in want.items():
            assert (sp[e]["first"], sp[e]["n_days"]) == (first, n), (m, e, sp[e])
            assert sp[e]["last"] == AS_OF, (m, e, sp[e])
        assert sp["oracle"] == sp["rain"] and sp["L0s"] == sp["L0"] and sp["L1s"] == sp["L1"], m
    # the protocol's words: ICON/GFS leads 1–5 ≈ 2024-01-20 →, ECMWF ≈ 2024-02-03 →, ICON lead 0 from 2022-11-16
    # (GFS's lead-1 rows wait for its lead-0 archive, which starts 2024-01-31: a lead-L row reads day I at lead 0)
    assert E.model_days("gfs_seamless", 1).query("complete")["date"].min() == pd.Timestamp("2024-01-20")
    assert E.model_days("gfs_seamless", 0).query("complete")["date"].min() == pd.Timestamp("2024-01-31")


# ── guards ──────────────────────────────────────────────────────────────────

def _raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc:
        return
    raise AssertionError(f"{fn.__name__}{a} did not raise {exc.__name__}")


def test_unknown_ids_and_spans_raise():
    _raises(KeyError, E.frames, "L6", ["avg"])
    _raises(KeyError, E.frames, "L2s", ["avg"])                       # protocol §3 lists L0s and L1s only
    _raises(KeyError, E.frames, "L1", ["avg"], model="best_match")
    _raises(KeyError, E.frames, "rain", ["US1CASF0017"])
    _raises(KeyError, E.frames, "rain", ["avg"], input_rules=["gauge_outage_v2"])
    _raises(KeyError, E.frames, "L1s", ["avg"], history="long")
    _raises(ValueError, E.frames, "L1", ["avg"], history="full")       # history is the as-served window
    _raises(ValueError, E.frames, "rain", ["avg", "avg"])
    _raises(ValueError, E.frames, "rain", [])
    _raises(ValueError, E.frames, "rain", ["avg"], end="2026-08-18")   # past the data end
    _raises(ValueError, E.frames, "rain", ["avg"], start="2016-02-29")  # before the training span
    _raises(ValueError, E.frames, "rain", ["avg"], start="2024-02-01", end="2024-01-01")
    _raises(KeyError, E.drops, "lead1")
    _raises(ValueError, E.model_peaks, "icon_seamless", 1, reach=6)
    for fn in (E.model_days, E.model_peaks):
        _raises(KeyError, fn, "icon_seamless", 6)
        _raises(KeyError, fn, "icon_seamless", "1")
    _raises(KeyError, E.nwp_hours, "icon_seamless", leads=(0, 6))
    _raises(KeyError, E.issue_time_unmasked, input_rules=["gauge_outage_v2"])


def test_every_entry_builds_in_minutes():
    """The full span × 2 sources × every entry (as served also under history_full_v1), from cold caches."""
    for fn in (E._lead_tables, E._gauge, E._training_features, E._days, E._peaks, E._hourly, E._per_gauge, E._hindsight,
               E._issue_records):
        fn.cache_clear()
    t0 = time.time()
    for e in E.ENTRIES:
        E.frames(e, ["avg", "SF Downtown"], model="icon_seamless")
        if E.as_served(e):
            E.frames(e, ["avg", "SF Downtown"], model="icon_seamless", history="full")
    took = time.time() - t0
    assert took < 180, f"{took:.0f} s"
    print(f"  every entry × 2 sources: {took:.1f} s")


if __name__ == "__main__":
    import traceback
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
                traceback.print_exc()
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
