"""Audit completed OANDA history without opening credentials or training models."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3

ASSETS = ('EUR_USD', 'USD_JPY', 'AUD_USD', 'CAD_JPY')


def audit(study):
    history = Path(study).resolve() / 'history'
    manifest = json.loads((history / 'manifest.json').read_text())
    if manifest['state'] != 'COMPLETED':
        raise ValueError('Historical download is incomplete; fitting remains blocked')
    start = datetime.fromisoformat(manifest['from'].replace('Z', '+00:00')).timestamp()
    end = datetime.fromisoformat(manifest['to_exclusive'].replace('Z', '+00:00')).timestamp()
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
              'history_state': manifest['state'], 'instruments': {},
              'training_performed': False,
              'limitation': 'Integrity checks do not prove complete market coverage or predictive ability.'}
    db = sqlite3.connect((history / 'candles.sqlite').as_uri() + '?mode=ro', uri=True)
    try:
        db.execute('BEGIN')
        for asset in ASSETS:
            rows = db.execute('SELECT t,side,o,h,l,c FROM candles WHERE instrument=? ORDER BY t,side',
                              (asset,)).fetchall()
            grouped = {}
            for t, side, o, h, l, c in rows:
                if t % 300 or not start <= t < end or t + 300 > end:
                    raise ValueError(f'{asset}: invalid timestamp')
                if not all(math.isfinite(v) and v > 0 for v in (o, h, l, c)) or not l <= min(o,c) <= max(o,c) <= h:
                    raise ValueError(f'{asset}: invalid OHLC')
                sides = grouped.setdefault(t, {})
                if side not in ('mid', 'bid', 'ask') or side in sides:
                    raise ValueError(f'{asset}: duplicate or unknown component')
                sides[side] = (o, h, l, c)
            if not grouped or any(set(s) != {'mid', 'bid', 'ask'} for s in grouped.values()):
                raise ValueError(f'{asset}: missing candle components')
            if any(any(b > a for b, a in zip(s['bid'], s['ask'])) for s in grouped.values()):
                raise ValueError(f'{asset}: crossed bid/ask candle values')
            file = history / (asset.replace('_', '') + '.csv')
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            if digest != manifest['data_hashes'][asset]:
                raise ValueError(f'{asset}: export hash differs from manifest')
            with file.open(newline='', encoding='utf-8-sig') as stream:
                exported = list(csv.DictReader(stream))
            expected = [(t, *s['mid']) for t, s in sorted(grouped.items())]
            actual = [(float(r['timestamp']), *(float(r[k]) for k in ('open','high','low','close')))
                      for r in exported]
            if actual != expected or len(expected) != manifest['instruments'][asset]['mid_candles']:
                raise ValueError(f'{asset}: CSV/database/count disagreement')
            times = sorted(grouped)
            gaps = [{'after_utc': datetime.fromtimestamp(a, timezone.utc).isoformat(),
                     'before_utc': datetime.fromtimestamp(b, timezone.utc).isoformat(),
                     'missing_five_minute_slots': (b-a)//300-1}
                    for a,b in zip(times,times[1:]) if b-a != 300]
            report['instruments'][asset] = {'candles_per_component': len(expected),
                'sha256': digest, 'csv_matches_database': True,
                'gap_count': len(gaps), 'missing_slots_between_observations': sum(g['missing_five_minute_slots'] for g in gaps),
                'gaps': gaps, 'first_timestamp': times[0], 'last_timestamp': times[-1]}
    finally:
        db.close()
    report['integrity'] = 'PASS'
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.study)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({'integrity': result['integrity'], 'candles_per_component':
        {a: r['candles_per_component'] for a,r in result['instruments'].items()}, 'report': str(args.out.resolve())}))
