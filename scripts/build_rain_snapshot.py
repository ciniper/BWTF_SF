"""Build the Admin rain explorer from downloaded AQPI CSVs, without network access.

python scripts/build_rain_snapshot.py --surfrider FILE --wastewater FILE \
    --manifest FILE --checked 2026-11-20 --output features/alerts/data/rain_snapshot.json
The source files remain outside the repository. Rebuild deliberately; this is
a dated research snapshot, not a live collector or a training-data update.
"""
from __future__ import annotations

import argparse
import calendar
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re

# Keep the standalone builder usable when called by file path.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.aqpi import REGIONS, SOURCES, build_feed, complete_sum


def archive_months(manifest):
    # Filenames are stamped at midnight AFTER the UTC day represented.
    names = re.findall(r'SURFRIDER_FOUNDATION_qpe15min_(\d{12})\.csv', Path(manifest).read_text())
    days = {(datetime.strptime(n, '%Y%m%d%H%M') - timedelta(days=1)).date() for n in names}
    if not days:
        raise ValueError('No archive files in manifest')
    first,last = min(days),max(days)
    months = []
    year,month = first.year,first.month
    while (year,month) <= (last.year,last.month):
        dates = [datetime(year,month,d).date() for d in range(1,calendar.monthrange(year,month)[1]+1)]
        months.append({'label': datetime(year,month,1).strftime('%b %Y'),
                       'days': [{'date': d.isoformat(), 'present': d in days} for d in dates],
                       'count': sum(d in days for d in dates)})
        year,month = (year+1,1) if month == 12 else (year,month+1)
    return {'count': len(days), 'start': first.isoformat(), 'end': last.isoformat(), 'months': months}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('surfrider','wastewater','manifest','output'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--checked', required=True, type=date.fromisoformat,
                        help='Date the source files were verified, YYYY-MM-DD')
    args = parser.parse_args()
    result = {'checked': args.checked.isoformat(), 'snapshot_label': args.checked.strftime('%b %d, %Y'),
              'regions': REGIONS, 'feeds': {key: build_feed(getattr(args,key),key) for key in SOURCES},
              'archive': archive_months(args.manifest)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,separators=(',',':'),allow_nan=False)+'\n')
    print(f'Wrote {args.output}: {args.output.stat().st_size:,} bytes')


if __name__ == '__main__':
    main()
