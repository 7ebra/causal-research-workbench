"""Deterministic offline mechanics demonstration. All prices are synthetic."""
import argparse
import csv
import html
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'research_lab'))
import screener as s
import forward as f


def run(out):
    out.mkdir(parents=True, exist_ok=False)
    rng = random.Random(23)
    rows, price = [], 1.1
    base = 1704067200
    for i in range(1500):
        close = price + rng.choice([-.0002, -.0001, .0001, .0002])
        rows.append(dict(t=base+i*s.BAR, open=price, high=max(price, close)+.0001,
                         low=min(price, close)-.0001, close=close))
        price = close
    states = s.features(rows)
    changed = [dict(r) for r in rows]
    for row in changed[800:]:
        for k in ('open', 'high', 'low', 'close'):
            row[k] *= 10
    gap_states = s.features(rows[:500]+rows[501:])
    ops = s.opportunities(rows, states, rows[400]['t'], rows[800]['t'], 3)
    checks = {
        'features_ignore_future': states[:800] == s.features(changed)[:800],
        'prefix_is_causal': states[:800] == s.features(rows[:800]),
        'gap_resets_warmup': all(x is None for x in gap_states[500:559]),
        'targets_stay_inside_partition': bool(ops) and all(rows[x]['t'] < rows[800]['t'] for e, x in ops.values()),
    }
    now = base+100*s.BAR+20
    def snapshot(asset, observed, quoted):
        bars = rows[:100]
        return dict(asset=asset, received_at=observed,
                    meta=dict(regularMarketTime=int(observed-1), regularMarketPrice=quoted),
                    timestamps=[r['t'] for r in bars],
                    quote={k: [r[k] for r in bars] for k in ('open','high','low','close')})
    assets = ('EURUSD','USDJPY','AUDUSD')
    state = states[99]
    models = {f'{a}-{m}': dict(asset=a, expiry_minutes=m, payout_assumed=.8,
              q={state:[0,1,0]}, support={state:[100,100,100]}, research_only=True)
              for a in assets for m in (5,10,15)}
    monitor = f.PaperMonitor(out/'synthetic.sqlite', models, now)
    try:
        monitor.tick([snapshot(a,now,1.12) for a in assets],now)
        before = monitor.db.execute('SELECT count(*) FROM trades').fetchone()[0]
        monitor.tick([snapshot(a,now+10,1.12) for a in assets],now+10)
        checks['duplicate_poll_does_not_duplicate_entries'] = before == monitor.db.execute('SELECT count(*) FROM trades').fetchone()[0]
        monitor.tick([snapshot(a,now+301,1.13) for a in assets],now+301)
        monitor.finish(now+310)
        cursor = monitor.db.execute('SELECT * FROM trades ORDER BY rowid')
        ledger = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
        checks['no_open_outcomes_after_finish'] = all(r['status'] != 'OPEN' for r in ledger)
        voids = [r for r in ledger if r['status'].startswith('VOID')]
        checks['voids_have_no_invented_pnl'] = bool(voids) and all(r['pnl'] is None for r in voids)
        settled = [r for r in ledger if not r['status'].startswith('VOID')]
        checks['settlement_quote_is_after_target'] = bool(settled) and all(r['exit_quote_t'] >= r['expiry_target'] for r in settled)
    finally:
        monitor.db.close()
    for name, data in [('synthetic-candles.csv',rows), ('synthetic-trades.csv',ledger)]:
        with (out/name).open('w',newline='',encoding='utf-8') as stream:
            writer = csv.DictWriter(stream,fieldnames=list(data[0]))
            writer.writeheader(); writer.writerows(data)
    report = dict(data_kind='SYNTHETIC_ONLY', policy='Forced fixture policy; not a trained strategy',
                  seed=23, candles=len(rows), engine_entries=len(ledger), checks=checks,
                  all_checks_passed=all(checks.values()), limits='Mechanics only. No market edge, broker outcome or investment result.')
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    body = ''.join(f'<tr><td>{html.escape(k)}</td><td>{v}</td></tr>' for k,v in checks.items())
    (out/'report.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Synthetic mechanics report</title><style>body{font:18px system-ui;max-width:900px;margin:40px auto;padding:20px;background:#f5f7fa;color:#182436}td{padding:12px;border-bottom:1px solid #ccc}</style><h1>Synthetic mechanics report</h1><p>Deterministic fixtures only. No market data or learned alpha.</p><table>'+body+'</table><p>'+html.escape(report['limits'])+'</p></html>',encoding='utf-8')
    print(json.dumps(report,indent=2))
    if not report['all_checks_passed']:
        raise SystemExit('Fixture checks failed')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    run(parser.parse_args().out)
