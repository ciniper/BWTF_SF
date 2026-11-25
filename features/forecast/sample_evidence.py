"""Apply current lab evidence to cached weather/model calculations, without I/O.

The collector is read once by the caller. No per-visitor upstream requests or
model inference. A passing panel needs all three City indicators; a group-wide
cap needs every registered group station on the same collection date, no newer
pending sample, and no current advisory. Missing evidence never means clean.
"""
from __future__ import annotations

import copy
import math
import re
from datetime import date, datetime, timedelta, timezone

from shared.standards import STANDARDS
from shared.stations import STATIONS
from shared.sfpuc_live import merge_records, MAX_AGE
from features.forecast.src.models.groups import SITE_GROUPS, GROUPS_BY_BASIN, ZONE_GROUPS, zone_risks
from features.forecast.src.models import live_rules as rules

POLICY = 'station_panels_v1'
REQUIRED = ('ENTERO', 'COLI_FECAL', 'COLI_TOTAL')
ZONE_OF_GROUP = {g: z for z, groups in ZONE_GROUPS.items() for g in groups}
BASIN_OF_GROUP = {g: b for b, groups in GROUPS_BY_BASIN.items() for g in groups}


def _bounds(raw):
    match = re.fullmatch(r'([<>]?)\s*(\d+(?:\.\d+)?)', str(raw).strip())
    if not match:
        return None
    sign, value = match[1], float(match[2])
    if not math.isfinite(value):
        return None
    return (0.0, value, False) if sign == '<' else (value, math.inf, True) if sign == '>' else (value, value, False)


def station_panel(rows):
    """Conservative interval grading; duplicate measurements all participate."""
    cells = {}
    for row in rows:
        bounds = _bounds(row.get('data'))
        if row.get('analyte') in STANDARDS and bounds is not None:
            cells.setdefault(row['analyte'], []).append((row, bounds))
    pending = [code for code in REQUIRED if code not in cells]
    elevated, uncertain = False, False
    for code, values in cells.items():
        limit = STANDARDS[code]['single_sample_max']
        for _, (lo, hi, lo_open) in values:
            if lo > limit or (lo == limit and lo_open):
                elevated = True
            elif hi > limit:
                uncertain = True
            if code == 'COLI_TOTAL' and hi > STANDARDS[code]['single_sample_max_ratio']:
                fecal = [b for _, b in cells.get('COLI_FECAL', [])]
                ratio = STANDARDS[code]['ratio_threshold']
                lower_limit = STANDARDS[code]['single_sample_max_ratio']
                if any((lo > lower_limit or (lo == lower_limit and lo_open)) and f[0] > ratio * hi for f in fecal):
                    elevated = True
                elif not fecal or any(f[1] > ratio * lo for f in fecal):
                    uncertain = True
    state = 'elevated' if elevated else 'partial' if pending or uncertain else 'below_limits'
    return {'state': state, 'pending': pending, 'complete': not pending, 'uncertain': uncertain,
            'indicators': {code: [{'raw': r['data'], 'source': 'getcsv' if r.get('early') else 'datasf'}
                                  for r, _ in values] for code, values in cells.items()}}


def build_evidence(datasf, early, beaches, now=None):
    now = now or datetime.now(timezone.utc)
    state = early.get('state', 'unavailable')
    # Recheck freshness even when a caller retains a snapshot object.
    try:
        age = now - datetime.fromisoformat(early['checked_at'].replace('Z', '+00:00'))
        if not timedelta(0) <= age <= MAX_AGE:
            state = 'stale'
    except (KeyError, TypeError, ValueError):
        state = 'unavailable'
    records = merge_records([r for r in datasf if _bounds(r.get('data')) is not None],
                            early.get('records', []) if state == 'current' else [])
    by_station = {}
    for row in records:
        source, day = row.get('source'), str(row.get('sample_date') or '')[:10]
        if source in STATIONS and day:
            by_station.setdefault(source, {}).setdefault(day, []).append(row)
    map_rows = {b.get('source'): b for b in beaches}
    panels = {}
    for source in STATIONS:
        days = by_station.get(source, {})
        beach = map_rows.get(source, {})
        latest = max([*days, early.get('known_dates', {}).get(source, ''), beach.get('sample_date_iso') or ''])
        panel = station_panel(days.get(latest, []))
        # A pending resample does not resolve the previous measured failure.
        failure_date = None
        if panel['state'] != 'below_limits':
            for day in sorted(days, reverse=True):
                previous = station_panel(days[day])
                if previous['state'] == 'below_limits':
                    break
                if previous['state'] == 'elevated':
                    failure_date = day
                    break
        panel.update(source=source, name=STATIONS[source].name, date=latest or None,
                     failure_date=failure_date,
                     advisory=bool(beach.get('has_cso') or beach.get('status') in ('posted', 'cso')))
        panels[source] = panel
    groups = {}
    for group, (_, sources) in SITE_GROUPS.items():
        stations = [panels[s] for s in sources]
        elevated = [p for p in stations if p['failure_date']]
        dates = {p['date'] for p in stations}
        ready = (state == 'current' and len(dates) == 1 and None not in dates
                 and all(p['state'] == 'below_limits' and not p['advisory'] for p in stations)
                 and all(map_rows.get(s, {}).get('status') in ('safe', 'not_sampled') for s in sources))
        groups[group] = {'state': 'elevated' if elevated else 'below_limits' if ready else 'partial',
                         'clean_eligible': ready, 'stations': stations}
    return groups, state


def overlay_snapshot(snapshot, early, now=None):
    """Recompute only the sample corrections from immutable stored inputs.

    Repeating this never compounds a correction. Losing early evidence removes
    its reduction immediately, even if the weather forecast is still cached.
    Old snapshots without the additive inputs remain readable as-is.
    """
    if 'sample_inputs' not in snapshot or not any(d.get('sample_model') for d in snapshot.get('predictions', {}).values()):
        return snapshot
    out = copy.deepcopy(snapshot)
    groups, state = build_evidence(out['sample_inputs'], early, out.get('beach_status', []), now)
    sample_flags = {}
    for group, evidence in groups.items():
        for panel in evidence['stations']:
            if panel['failure_date']:
                sample_flags[(group, date.fromisoformat(panel['failure_date']))] = True
        if evidence['clean_eligible']:
            sample_flags[(group, date.fromisoformat(evidence['stations'][0]['date']))] = False
    for key, day in out.get('predictions', {}).items():
        model = day.get('sample_model')
        if not model:
            continue
        target = date.fromisoformat(key)
        probs = {date.fromisoformat(k): v for k, v in model['probs'].items()}
        onsets = {date.fromisoformat(k): set(v) for k, v in model['onsets'].items()}
        flag_groups = {date.fromisoformat(k): set(v) for k, v in model['flag_groups'].items()}
        flags = {date.fromisoformat(k): set(v) for k, v in model['flags'].items()}
        # Retain posted-sample evidence when lab measurements are unavailable.
        # Only a complete, clear group can supersede it on the same date.
        combined_samples = {(r['group'], date.fromisoformat(r['date'])): True
                            for r in model.get('feed_samples', [])}
        combined_samples.update(sample_flags)
        adjusted, notes = rules.adjust_groups(model['groups'], model['persist'], model['today_terms'], target,
            combined_samples, probs, onsets, flags, ZONE_OF_GROUP, BASIN_OF_GROUP,
            lambda g, k: model['large_curves'][g][str(k)], flag_groups=flag_groups)
        day['impact_groups'] = adjusted
        day['zones'] = zone_risks(adjusted)
        day['predictions'] = {b: max((adjusted[g] for g in gs), default=0.) for b, gs in GROUPS_BY_BASIN.items()}
        day['predictions']['citywide'] = max(day['predictions'].values(), default=0.)
        day['live_corrections']['groups'] = notes
        day['sample_evidence'] = groups
    out['sample_evidence_status'] = {'policy': POLICY, 'state': state, 'checked_at': early.get('checked_at')}
    return out
