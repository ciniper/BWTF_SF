"""Record SFPUC's beach feeds during a discharge, so we keep what a discharge
and a result day look like in the raw API.

Three feeds, each written only when it changes:

- getBeaches (the map feed: postings, CSO flags, sample dates), read every
  --every seconds. Each snapshot also stores our own /api/realtime from the same
  moment, so the record shows how we interpreted it.
- getCSV (the bacteria numbers for every station in one call: total/fecal
  coliform, Enterococcus), keyed by outfall/source code.
- getStation (the same numbers, one call per station, for all 20 stations),
  keyed by station id. Read back to back with getCSV every --station-every
  seconds, so the log shows whether the two ever disagree on timing.
  Each snapshot keeps a station's two newest sample dates; a change is a new
  sample date or an indicator filling in.

Every change is also logged in one line to changes.log, so a result day reads
in order: posting flip, numbers, map sample date.

    python3 scripts/record_sfpuc_feed.py --out docs/discharge-observations/feed --hours 72

Stdlib only, so it runs without the project's requirements installed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

LIMS = "https://infrastructure.sfwater.org/lims.asmx"
OURS_URL = "https://bwtf-sf.vercel.app/api/realtime"
FIELDS = ("cso", "s_color", "posted", "p_color", "sample_date")   # what can change per station in getBeaches
INDICATORS = ("COLI_TOTAL", "COLI_FECAL", "ENTERO", "COLI_E")
KEEP_ROWS = 2   # newest sample dates per station kept in a getCSV / getStation snapshot
STATION_IDS = [str(i) for i in range(4601, 4621)]   # all 20; the 3 not-routine ones get storm samples
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def get(url: str, timeout: int = 45) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8")


def lims_json(op: str, **params) -> list[dict]:
    url = f"{LIMS}/{op}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    raw = get(url).strip()
    if raw.startswith("["):   # getCSV answers bare JSON
        return json.loads(raw)
    m = re.search(r">(\[.*\])<", raw, re.S)
    if not m:
        raise ValueError(f"unexpected {op} body: {raw[:200]!r}")
    return json.loads(m.group(1))


def read_csv() -> dict[str, list[dict]]:
    """{source: [{"sample_date", COLI_TOTAL, COLI_FECAL, ENTERO, ...}, ...]}, newest first,
    pivoted from getCSV's one-row-per-analyte shape. Covers every station with samples,
    including the "not routinely sampled" ones when SFPUC samples them in a discharge."""
    by: dict[str, dict[str, dict]] = {}
    for r in lims_json("getCSV"):
        if r.get("sample_date"):
            by.setdefault(r["source"], {}).setdefault(r["sample_date"], {"sample_date": r["sample_date"]})[r["analyte"]] = r.get("data")
    def when(d):
        return datetime.strptime(d, "%m/%d/%y")
    return {src: sorted(rows.values(), key=lambda x: when(x["sample_date"]), reverse=True)[:KEEP_ROWS]
            for src, rows in sorted(by.items())}


def read_getstation() -> dict[str, list[dict]]:
    """{station id: [rows newest first]} from getStation, one call per station."""
    out = {}
    for sid in STATION_IDS:
        out[sid] = lims_json("getStation", stationId=sid)[:KEEP_ROWS]
        time.sleep(0.3)   # gentle on SFPUC
    return out


def h(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def beaches_key(stations: list[dict]) -> str:
    return h(sorted((s.get("stationid"), [s.get(f) for f in FIELDS]) for s in stations))


def beaches_diff(old: list[dict] | None, new: list[dict]) -> list[str]:
    before = {s["stationid"]: s for s in (old or [])}
    out = []
    for s in new:
        p = before.get(s["stationid"])
        changed = [f"{f} {p.get(f) if p else '∅'}→{s.get(f)}" for f in FIELDS if not p or p.get(f) != s.get(f)]
        if changed:
            out.append(f"{s['stationid']} {s['stationname']}: " + ", ".join(changed))
    return out


def station_diff(old: dict | None, new: dict) -> list[str]:
    out = []
    for sid, rows in new.items():
        prev = {r.get("sample_date"): r for r in (old or {}).get(sid, [])}
        for r in rows:
            p = prev.get(r.get("sample_date"))
            vals = ", ".join(f"{i} {r.get(i)}" for i in INDICATORS if r.get(i) is not None)
            name = " ".join(x for x in (sid, r.get("stationname"), r.get("sample_date")) if x)
            if p is None:
                out.append(f"{name}: new row ({vals or 'no values yet'})")
            else:
                changed = [f"{i} {p.get(i)}→{r.get(i)}" for i in INDICATORS if p.get(i) != r.get(i)]
                if changed:
                    out.append(f"{name}: " + ", ".join(changed))
    return out


def latest(out: Path, suffix: str):
    snaps = sorted(out.glob(f"*-{suffix}.json"))
    return json.loads(snaps[-1].read_text()) if snaps else None


def write(out: Path, log: Path, now: datetime, suffix: str, body: dict, lines: list[str], label: str):
    stamp = now.strftime("%Y-%m-%d-%H%M%S")
    (out / f"{stamp}-{suffix}.json").write_text(json.dumps({"read_at": now.isoformat(timespec="seconds"), **body}, indent=1))
    with log.open("a") as f:
        f.write(f"{now.isoformat(timespec='seconds')}  [{label}] {stamp}-{suffix}.json\n")
        f.writelines(f"    {l}\n" for l in lines)
    print(stamp, label, f"{len(lines)} change(s)", flush=True)


def note_failure(log: Path, label: str, e: Exception):
    with log.open("a") as f:
        f.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')}  [{label}] read failed: {e}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/discharge-observations/feed")
    ap.add_argument("--every", type=int, default=120, help="seconds between getBeaches reads")
    ap.add_argument("--station-every", type=int, default=120, help="seconds between getCSV + getStation reads (0 = off)")
    ap.add_argument("--hours", type=float, default=72, help="stop after this long")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    log = out / "changes.log"

    # pick up where an earlier run left off
    prev = latest(out, "getBeaches")
    beaches = prev["feed"] if prev else None
    numbers = {}   # label -> (reader, last snapshot)
    for label, reader in (("getCSV", read_csv), ("getStation", read_getstation)):
        prev = latest(out, label)
        numbers[label] = (reader, prev["stations"] if prev else None)
    next_station = 0.0

    stop = time.time() + a.hours * 3600
    while time.time() < stop:
        now = datetime.now().astimezone()
        try:
            feed = lims_json("getBeaches")
            if beaches is None or beaches_key(feed) != beaches_key(beaches):
                try:
                    ours = json.loads(get(OURS_URL))
                except Exception as e:
                    ours = {"error": str(e)}
                lines = beaches_diff(beaches, feed) if beaches is not None else ["first snapshot"]
                write(out, log, now, "getBeaches", {"feed": feed, "ours_api_realtime": ours}, lines, "getBeaches")
                beaches = feed
        except Exception as e:   # keep going through outages; note them
            note_failure(log, "getBeaches", e)

        if a.station_every and time.time() >= next_station:
            next_station = time.time() + a.station_every
            for label, (reader, last) in list(numbers.items()):
                now = datetime.now().astimezone()
                try:
                    snap = reader()
                    if last is None or h(snap) != h(last):
                        lines = station_diff(last, snap) if last is not None else ["first snapshot"]
                        write(out, log, now, label, {"stations": snap}, lines, label)
                        numbers[label] = (reader, snap)
                except Exception as e:
                    note_failure(log, label, e)

        time.sleep(a.every)
    return 0


if __name__ == "__main__":
    sys.exit(main())
