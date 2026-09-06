#!/usr/bin/env python3
"""
Beach_Poo_Bot archive → training data (2016-03 → 2017-01).

John Brandon's Twitter bot (github.com/John-Brandon/Beach_Poo_Bot) committed
a snapshot of SFPUC's LIMS ``getCSV`` feed roughly twice a day from
2016-03-19 to 2017-01-10 — 552 files. Each snapshot carries ~3 months of lab
results (ENTERO / COLI_E / COLI_TOTAL per station) plus, when active, the
feed's status rows: ``POSTED`` station ids (with ``COLOR`` R) and ``CSO``
structure names ("ISLAIS CREEK", "SEA CLIFF II", "BAKER STREET CSD09", ...).

This module turns the archive into three CSVs under data/poobot/:

  samples.csv          unique (source, sample_date, analyte) results,
                       Dec 2015 – Jan 2017 — 3.5 years before DataSF's floor
  feed_status.csv      one row per snapshot: active CSO structures and posted
                       stations (the empirical structure → station evidence
                       behind shared/outfalls.py)
  discharge_onsets.csv structure onsets (newly active vs the previous
                       snapshot) with the outfall ids and app basin they map
                       to — Westside labels for Oct 2016 – Jan 2017, which
                       CIWQS does not cover before 2018

Run:  venv/bin/python features/forecast/src/collectors/poobot_archive.py [cache_dir]
      (no argument = fetch all 552 snapshots from GitHub)
"""
from __future__ import annotations

import csv
import io
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from shared.outfalls import FEED_NAME_TO_OUTFALLS, OUTFALLS  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "poobot"
GITHUB_API = "https://api.github.com/repos/John-Brandon/Beach_Poo_Bot/contents/data"
RAW_BASE = "https://raw.githubusercontent.com/John-Brandon/Beach_Poo_Bot/master/data/"
HEADERS = {"User-Agent": "bwtf-sf (poobot archive ingest)"}
_TS = re.compile(r"(\d{4}-\d{2}-\d{2})[ _](\d{2})[:-](\d{2})[:-](\d{2})")


def snapshot_names() -> list:
    items = requests.get(GITHUB_API, headers=HEADERS, timeout=60).json()
    return sorted(i["name"] for i in items if i["name"].startswith("lims_") and i["name"].endswith(".csv"))


def fetch_snapshot(name: str) -> str:
    r = requests.get(RAW_BASE + requests.utils.quote(name), headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.text


def parse_snapshot(name: str, text: str):
    ts = _TS.search(name)
    snap = f"{ts.group(1)} {ts.group(2)}:{ts.group(3)}:{ts.group(4)}" if ts else name
    rows = list(csv.DictReader(io.StringIO(text)))
    cso = sorted({(r.get("CSO") or "").strip() for r in rows} - {"", "NA"})
    posted = sorted({(r.get("POSTED") or "").strip() for r in rows} - {"", "NA"})
    samples = [(r["SOURCE"].strip(), r["SAMPLE_DATE"][:10], r["ANALYTE"].strip(), (r.get("DATA") or "").strip())
               for r in rows if r.get("ANALYTE") and r.get("SAMPLE_DATE") and r.get("SOURCE")]
    return snap, cso, posted, samples


def build(texts: dict) -> dict:
    """texts: {snapshot filename: csv text}. Returns the three tables."""
    status, samples = [], {}
    for name in sorted(texts):
        snap, cso, posted, smp = parse_snapshot(name, texts[name])
        status.append({"snapshot": snap, "cso_structures": "|".join(cso), "posted_stations": "|".join(posted)})
        for src, d, an, val in smp:
            samples.setdefault((src, d, an), {"source": src, "sample_date": d, "analyte": an,
                                              "data": val, "first_seen": snap})
    prev, onsets = set(), []
    for row in status:
        active = set(filter(None, row["cso_structures"].split("|")))
        for name in sorted(active - prev):
            oids = FEED_NAME_TO_OUTFALLS.get(name, [])
            basins = sorted({OUTFALLS[o].basin for o in oids})
            onsets.append({"date": row["snapshot"][:10], "snapshot": row["snapshot"], "structure": name,
                           "outfall_ids": "|".join(oids), "basin": "|".join(basins), "mapped": bool(oids)})
        prev = active
    return {"samples": sorted(samples.values(), key=lambda r: (r["sample_date"], r["source"], r["analyte"])),
            "feed_status": status, "discharge_onsets": onsets}


def write(tables: dict, out_dir: Path = OUT_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        if not rows:
            continue
        with open(out_dir / f"{name}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)


def main(cache_dir: str | None = None) -> None:
    texts = {}
    if cache_dir:
        for p in sorted(Path(cache_dir).glob("lims_*.csv")):
            texts[p.name] = p.read_text(errors="ignore")
    else:
        from concurrent.futures import ThreadPoolExecutor
        names = snapshot_names()
        with ThreadPoolExecutor(8) as pool:
            for name, text in zip(names, pool.map(fetch_snapshot, names)):
                texts[name] = text
    tables = build(texts)
    write(tables)
    s, o = tables["samples"], tables["discharge_onsets"]
    print(f"{len(texts)} snapshots → {len(s)} samples ({s[0]['sample_date']} → {s[-1]['sample_date']}), "
          f"{len(tables['feed_status'])} status rows, {len(o)} onsets ({sum(1 for r in o if r['mapped'])} mapped)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
