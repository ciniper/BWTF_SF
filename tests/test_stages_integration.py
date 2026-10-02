"""Wave 2 of the stages build fits together: truth.py, exclusions.py and compose_v2.py read one
geography and one truth (STAGES_DESIGN.md Part C §2.6, §3.3, §4.1, §8 P4 / P4b / P6; Part B 2, 22).

Each module has its own tests; these pin the joins between them, which no single module's tests see:
  - every new module imports on its own, opens no data file at import, and none imports another in a
    cycle (truth ← exclusions; compose_v2 stands alone on shared/geography and impact);
  - compose_v2's link ids and zone keys are the geography's, and so are the GEO_V1 adapter's units;
  - compose_v2 counts co-firing from the same link onsets truth.link_onsets files (it reads the ledger
    rows itself, so a change in how truth places an event would otherwise split the two);
  - the S3 oracle on identity zones equals truth.zone_overflow on every known day: §3.3's integrity
    check (X-S3-ID: mismatches must be 0), run through compose_v2 against truth;
  - the exclusions context's zone, basin and OUT truth is truth.py's, never re-derived;
  - the figure's inset is drawn from shared/geography.py alone (the P2 fallback table is gone).

Counts are as of 2026-08-17, the committed data's end (Part B 23).

    venv/bin/python tests/test_stages_integration.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FORECAST = ROOT / "features" / "forecast"
MODELS = FORECAST / "src" / "models"
COLLECTORS = FORECAST / "src" / "collectors"
for p in (ROOT, MODELS, COLLECTORS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import compose_v2 as C  # noqa: E402
import csd_labels  # noqa: E402
import exclusions as X  # noqa: E402
import stages_flowchart as F  # noqa: E402
import stages_spec as SP  # noqa: E402
import truth as T  # noqa: E402
from shared import geography as G  # noqa: E402
from shared.zones import ZONES  # noqa: E402

AS_OF = pd.Timestamp("2026-08-17")     # the committed data's end: every count below is as of this day
NEW_MODULES = ("truth", "exclusions", "compose_v2", "registry_check")


def _same(a, b) -> bool:
    """Equal as nullable values: <NA> / NaN on both sides counts as equal."""
    a = pd.Series(a, dtype="float").to_numpy()
    b = pd.Series(b, dtype="float").to_numpy()
    return bool(((a == b) | (np.isnan(a) & np.isnan(b))).all())


# ── imports ─────────────────────────────────────────────────────────────────

def test_each_new_module_imports_alone_and_reads_no_data():
    """In a fresh interpreter each module imports on its own (no cycle needs another imported first)
    and opens nothing under features/forecast/data while it does."""
    paths = [str(ROOT), str(MODELS), str(COLLECTORS), str(COLLECTORS / "csd_ciwqs")]
    for mod in NEW_MODULES:
        code = ("import builtins, io, sys, pathlib; sys.path[:0] = %r\n"
                "real = io.open\n"
                "def spy(f, *a, **k):\n"
                "    if '/features/forecast/data/' in str(f): raise AssertionError(f'opened {f} at import')\n"
                "    return real(f, *a, **k)\n"
                "builtins.open = io.open = spy\n"
                "for name in ('read_text', 'read_bytes'):\n"
                "    orig = getattr(pathlib.Path, name)\n"
                "    def wrap(self, *a, _o=orig, **k):\n"
                "        if '/features/forecast/data/' in str(self): raise AssertionError(f'read {self} at import')\n"
                "        return _o(self, *a, **k)\n"
                "    setattr(pathlib.Path, name, wrap)\n"
                "import %s\n"
                "print('inert')") % (paths, mod)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env={**os.environ, "SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""})
        assert r.returncode == 0 and r.stdout.strip().endswith("inert"), (mod, r.stderr[-800:])


def test_the_import_graph_has_no_cycle():
    """truth imports neither exclusions nor compose_v2; compose_v2 imports neither truth nor exclusions."""
    import ast

    def imports(path: Path) -> set:
        names = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        return names

    got = {m: imports(MODELS / f"{m}.py") for m in ("truth", "exclusions", "compose_v2")}
    assert not got["truth"] & {"exclusions", "compose_v2"}, got["truth"]
    assert not got["compose_v2"] & {"truth", "exclusions"}, got["compose_v2"]
    assert "truth" in got["exclusions"] and "compose_v2" not in got["exclusions"], got["exclusions"]


# ── compose_v2 and the geography ────────────────────────────────────────────

def test_compose_ids_are_the_geographys():
    ev = csd_labels.load_events()
    days = pd.date_range("2022-12-20", "2023-01-31")       # every basin overflowed in these storms
    for geo in (G.GEO_V1, G.SFPUC4_V1):
        ids = [lk.id for lk in geo.links]
        spec = C.benchmark_s3_spec(geo, "identity", shares=C.cofire(geo, ev, end=AS_OF))
        assert list(spec["links"]) == ids, (geo.version, list(spec["links"]))
        p = pd.DataFrame(0.3, index=days, columns=list(geo.keys))
        out = C.s3(geo, spec, p, p * 0 + 1.0)
        assert list(out.link_p.columns) == ids, (geo.version, list(out.link_p.columns))
        assert list(out.zone_p.columns) == list(ZONES) == sorted({lk.zone for lk in geo.links}, key=list(ZONES).index)
    ad = C.geo_v1_adapter_specs()
    assert list(ad["s3"]["links"]) == [lk.id for lk in G.GEO_V1.links]
    assert list(C.s4_units(G.GEO_V1, ad["s4"])) == [lk.id for lk in G.GEO_V1.links]


def test_compose_counts_cofiring_on_truths_link_onsets():
    """compose_v2.link_fire_days (from the ledger rows) = truth.link_onsets y == 1, every link, both
    geographies, 2016-03-01 → 2026-08-17. Link onset days then: 43 / 54 / 42 / 97 / 23 under SFPUC4."""
    ev = csd_labels.load_events()
    for geo in (G.GEO_V1, G.SFPUC4_V1):
        fired = C.link_fire_days(geo, ev, end=AS_OF)
        lo = T.link_onsets(geo, T.TRUTH_START, AS_OF)
        on = lo[lo["y"].eq(1).fillna(False)]
        for lk in geo.links:
            truth_days = set(on.loc[on["link"] == lk.id, "date"])
            compose_days = {d for d in fired[lk.id] if d >= T.TRUTH_START}
            assert compose_days == truth_days, (geo.version, lk.id, sorted(compose_days ^ truth_days)[:5])
    n = {lk: int((T.link_onsets(G.SFPUC4_V1, T.TRUTH_START, AS_OF).query("link == @lk")["y"] == 1).sum())
         for lk in (k.id for k in G.SFPUC4_V1.links)}
    assert list(n.values()) == [43, 54, 42, 97, 23], n


def test_s3_oracle_on_identity_zones_is_the_zone_truth():
    """X-S3-ID's integrity check (§3.3; protocol §7): fed the true basin occurrence, compose_v2's identity
    zones equal truth.zone_overflow on every day the zone's truth is known: 0 mismatches."""
    ev = csd_labels.load_events()
    checked = {}
    for geo in (G.GEO_V1, G.SFPUC4_V1):
        bo = T.basin_onsets(geo, T.TRUTH_START, AS_OF)
        y = bo.pivot(index="date", columns="basin", values="y").astype("float")[list(geo.keys)]
        known = y.notna()
        spec = C.benchmark_s3_spec(geo, "identity", shares=C.cofire(geo, ev, end=AS_OF))
        inputs = C.BasinInputs(y.fillna(0.0) * 0 + 0.5, y.fillna(0.0) * 0 + 1.0).oracle(y.fillna(0.0))
        zp = C.s3(geo, spec, inputs.p, inputs.v_hat).zone_p
        zo = T.zone_overflow(geo, T.TRUTH_START, AS_OF)
        for z in ZONES:
            if not all(lk.identity for lk in geo.links_into(z)):
                continue
            t = zo[zo["zone"] == z].set_index("date")
            k = t["known"].to_numpy(dtype=bool)
            assert (k == known[list(T.feeding_basins(geo, z))].all(axis=1).to_numpy()).all(), (geo.version, z)
            got, want = zp[z].to_numpy()[k], t["y"].astype("float").to_numpy()[k]
            assert (got == want).all(), (geo.version, z, int((got != want).sum()))
            checked[(geo.version, z)] = int(want.sum())
    # SFPUC4: North Shore → North and Central + South → East are identity; GEO_V1 East (two one-link basins)
    assert checked == {("geo_v1", "east"): 100, ("sfpuc4_v1", "north"): 42, ("sfpuc4_v1", "east"): 100}, checked


# ── exclusions reads truth ──────────────────────────────────────────────────

def test_the_exclusions_context_is_truths():
    for geo in (G.GEO_V1, G.SFPUC4_V1):
        ctx = X.context(geo, end=AS_OF)
        zone = ctx.frames["zone"]
        zo = T.zone_overflow(geo, T.TRUTH_START, AS_OF).set_index(["zone", "date"])
        ol = T.out_label(geo, start=T.TRUTH_START, end=AS_OF).set_index(["zone", "date"])
        zone = zone.reindex(zo.index.rename(["unit", "date"]))
        assert len(zone) == len(zo) == len(ZONES) * len(pd.date_range(T.TRUTH_START, AS_OF))
        assert _same(zone["y"], zo["y"]), geo.version
        assert (zone["known"].to_numpy() == zo["known"].to_numpy()).all()
        assert (zone["hist_known"].to_numpy() == zo["hist_known"].to_numpy()).all()
        assert (zone["geo_only"].to_numpy() == zo["only_geography"].to_numpy()).all()
        assert _same(zone["out_y"], ol["y"]) and (zone["out_why"].to_numpy() == ol["why"].to_numpy()).all()
        basin = ctx.frames["basin"]
        bo = T.basin_onsets(geo, T.TRUTH_START, AS_OF).set_index(["basin", "date"])
        basin = basin.reindex(bo.index.rename(["unit", "date"]))
        assert _same(basin["y"], bo["y"]) and (basin["known"].to_numpy() == bo["known"].to_numpy()).all()
        assert sorted(set(ctx.frames["link"].index.get_level_values(0))) == sorted(lk.id for lk in geo.links)
        assert set(ctx.frames["basin"].index.get_level_values(0)) == set(geo.keys)


# ── the figure ──────────────────────────────────────────────────────────────

def test_the_figure_inset_reads_the_geography_only():
    assert not hasattr(SP, "FALLBACK_INSET") and "fallback_inset" not in SP.spec_dict()
    for geo in (G.GEO_V1, G.SFPUC4_V1):
        ins = F.inset_data(geo.version)
        assert ins["source"] == geo.version, ins["source"]
        assert ins["basin_full"] == [b.name for b in geo.basins]
    try:
        F.inset_data("sfpuc4")
        raise AssertionError("an unknown geography drew an inset")
    except KeyError:
        pass


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
