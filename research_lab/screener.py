"""Reproducible FX reference-data RL experiment; Python standard library only."""
from __future__ import annotations
import argparse
import bisect
import csv
import hashlib
import json
import math
import random
import shutil
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BAR = 300
ACTIONS = ('SKIP', 'UP', 'DOWN')


def stamp(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def load_candles(path):
    rows = []
    with open(path, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            c = {k: float(row[k]) for k in ('open', 'high', 'low', 'close')}
            raw_t = float(row['timestamp'])
            if not math.isfinite(raw_t) or raw_t != int(raw_t):
                raise ValueError('Timestamps must be integer Unix seconds')
            c['t'] = int(raw_t)
            if c['t'] % BAR or (rows and c['t'] <= rows[-1]['t']):
                raise ValueError('Candles must be unique, sorted, five-minute UTC bars')
            if not all(math.isfinite(v) and v > 0 for k, v in c.items() if k != 't'):
                raise ValueError('Invalid OHLC price')
            if not c['low'] <= min(c['open'], c['close']) <= max(c['open'], c['close']) <= c['high']:
                raise ValueError('Inconsistent OHLC candle')
            rows.append(c)
    if len(rows) < 1000:
        raise ValueError('At least 1000 closed candles are required')
    return rows


def bucket(x, threshold):
    return -1 if x < -threshold else 1 if x > threshold else 0


def features(rows):
    """Only completed bars <= i; reset the 60-bar warmup after any gap."""
    states = [None] * len(rows)
    ranges = [max(r['high'] - r['low'], r['close'] * 1e-8) for r in rows]
    run = 0
    for i, r in enumerate(rows):
        run = run + 1 if i and r['t'] - rows[i-1]['t'] == BAR else 1
        if run < 60:
            continue
        atr = statistics.fmean(ranges[i-11:i+1])
        slow = statistics.fmean(ranges[i-59:i+1])
        state = (bucket((r['close'] - rows[i-1]['close']) / atr, .25),
                 bucket((r['close'] - rows[i-3]['close']) / atr, .5),
                 bucket((r['close'] - r['open']) / ranges[i], .35),
                 int(atr > slow))
        states[i] = ','.join(map(str, state))
    return states


def opportunities(rows, states, start, end, horizon, delay=0):
    """Entry is next bar open, settlement a later open, both inside [start,end)."""
    result = {}
    for i in range(len(rows) - horizon - delay - 1):
        entry, expiry = i + 1 + delay, i + 1 + delay + horizon
        if states[i] is None or rows[i]['t'] + BAR < start:
            continue
        if rows[entry]['t'] < start or rows[expiry]['t'] >= end:
            continue
        if rows[expiry]['t'] - rows[i]['t'] != BAR * (expiry-i):
            continue  # Never fill a missing bar or bridge a weekend.
        result[i] = (entry, expiry)
    return result


def payoff(entry, exit_price, action, payout, cost=0):
    if action == 0:
        return 0.0
    delta = exit_price - entry
    return (0.0 if delta == 0 else payout if (delta > 0) == (action == 1) else -1.0) - cost


def train(rows, states, start, end, horizon, payout, seed, epochs=12):
    """Tabular semi-Markov Q-learning with a per-bar discount of .98.

    A trade occupies horizon+1 bars. Skip occupies one. Each run sees only the
    training interval. Support counts unique decision timestamps, not repeats.
    """
    rng, q, support = random.Random(seed), {}, defaultdict(lambda: [set(), set(), set()])
    ops = opportunities(rows, states, start, end, horizon)
    keys = sorted(ops)
    if not keys:
        raise ValueError('No continuous training windows')
    for epoch in range(epochs):
        pos = 0
        epsilon = .35 - .30 * epoch / max(1, epochs-1)
        while pos < len(keys):
            i = keys[pos]
            state = states[i]
            values = q.setdefault(state, [0.0, 0.0, 0.0])
            action = rng.randrange(3) if rng.random() < epsilon else max(range(3), key=lambda a: values[a])
            entry, expiry = ops[i]
            next_i = i+1 if action == 0 else expiry
            next_pos = bisect.bisect_left(keys, next_i)
            reward = payoff(rows[entry]['open'], rows[expiry]['open'], action, payout)
            future = 0.0
            # End each contiguous market session as a terminal transition.
            if next_pos < len(keys) and keys[next_pos] == next_i:
                future = max(q.get(states[next_i], [0.0]*3)) * .98 ** (next_i-i)
            values[action] += .08 * (reward + future - values[action])
            support[state][action].add(rows[i]['t'])
            pos = next_pos
    return {'q': q, 'support': {s: [len(x) for x in v] for s,v in support.items()}, 'seed': seed}


def combine(models):
    keys = set().union(*(m['q'] for m in models))
    return {'q': {s: [statistics.fmean(m['q'].get(s, [0.0]*3)[a] for m in models) for a in range(3)] for s in keys},
            'support': {s: [min(m['support'].get(s,[0]*3)[a] for m in models) for a in range(3)] for s in keys}}


def explain_choice(model, state, min_support=30):
    """Expose policy abstention without changing its frozen action thresholds."""
    if state is None:
        return {'action': 0, 'reason': 'GAP_OR_WARMUP'}
    if state not in model['q']:
        return {'action': 0, 'reason': 'UNSEEN_STATE'}
    values = model['q'][state]
    action = max(range(3), key=lambda a: values[a])
    detail = {'action': action, 'raw_action': ACTIONS[action], 'q': list(values),
              'selected_support': model['support'][state][action],
              'q_advantage_over_skip': values[action]-values[0],
              'min_support': min_support, 'min_q_advantage': .02}
    if action == 0:
        detail['reason'] = 'Q_PREFERS_SKIP'
    elif values[action] <= values[0] + .02:
        detail.update(action=0, reason='INSUFFICIENT_Q_ADVANTAGE')
    elif model['support'][state][action] < min_support:
        detail.update(action=0, reason='LOW_SUPPORT')
    else:
        detail['reason'] = 'MODEL_WANTS_TRADE'
    return detail


def choose(model, state, min_support=30):
    return explain_choice(model, state, min_support)['action']


def daily_ci(values, seed=42):
    if len(values) < 2:
        return [None, None]
    rng = random.Random(seed)
    samples = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(1500))
    return [samples[37], samples[1462]]


def evaluate(rows, states, model, start, end, horizon, payout=.8, policy='rl', delay=0, cost=0):
    ops = opportunities(rows, states, start, end, horizon, delay)
    ledger, daily = [], {}
    balance = peak = 1000.0
    stake, max_dd, next_i = 10.0, 0.0, 0
    stopped = False
    rng = random.Random(871)
    for i, (entry, expiry) in ops.items():
        day = stamp(rows[entry]['t'])[:10]
        daily.setdefault(day, 0.0)
        if i < next_i or stopped or daily[day] <= -30.0 or balance < stake:
            continue
        if policy == 'rl':
            action = choose(model, states[i])
        elif policy == 'momentum':
            action = 1 if rows[i]['close'] > rows[i-3]['close'] else 2
        elif policy == 'random':
            action = rng.choice((1, 2))
        else:
            action = {'up': 1, 'down': 2, 'skip': 0}[policy]
        if action == 0:
            continue
        entry_price, exit_price = rows[entry]['open'], rows[expiry]['open']
        net = payoff(entry_price, exit_price, action, payout, cost) * stake
        balance += net
        daily[day] += net
        peak = max(peak, balance)
        max_dd = max(max_dd, peak-balance)
        # A fixed 100-unit loss budget from initial balance, no stake escalation.
        stopped = balance <= 900.0
        ledger.append({'decision_time': stamp(rows[i]['t']+BAR),
                       'entry_time': stamp(rows[entry]['t']), 'expiry_time': stamp(rows[expiry]['t']),
                       'direction': ACTIONS[action], 'entry': entry_price, 'exit': exit_price,
                       'state': states[i], 'stake': stake, 'payout_assumed': payout,
                       'pnl': round(net, 8), 'balance': round(balance, 8),
                       'result': 'TIE' if entry_price == exit_price else 'WIN' if net > 0 else 'LOSS'})
        next_i = expiry  # Next decision after the settlement bar closes.
    wins = sum(t['result'] == 'WIN' for t in ledger)
    ties = sum(t['result'] == 'TIE' for t in ledger)
    n = len(ledger)
    return {'trades': n, 'wins': wins, 'ties': ties, 'losses': n-wins-ties,
            'win_rate_excluding_ties': wins/(n-ties) if n > ties else None,
            'pnl': round(balance-1000, 8), 'ending_balance': round(balance, 8),
            'return_on_stakes': (balance-1000)/(n*stake) if n else None,
            'max_drawdown': round(max_dd, 8), 'market_days': len(daily),
            'daily_mean_ci95': daily_ci(list(daily.values())),
            'total_loss_stop_reached': stopped, 'daily': daily, 'ledger': ledger}


def compact(result):
    return {k: v for k, v in result.items() if k not in ('ledger', 'daily')}


def quality(rows):
    return {'candles': len(rows), 'gaps': sum(b['t']-a['t'] != BAR for a,b in zip(rows,rows[1:])),
            'first': stamp(rows[0]['t']), 'last': stamp(rows[-1]['t']),
            'zero_range_fraction': sum(r['high'] == r['low'] for r in rows)/len(rows),
            'unchanged_body_fraction': sum(r['open'] == r['close'] for r in rows)/len(rows)}


def qualify(result, days):
    reasons = []
    if result['trades'] < 100:
        reasons.append('Fewer than 100 simulated trades')
    if result['market_days'] < days:
        reasons.append(f'Fewer than {days} market days')
    if result['daily_mean_ci95'][0] is None or result['daily_mean_ci95'][0] <= 0:
        reasons.append('Daily profit confidence interval does not stay above zero')
    if result['total_loss_stop_reached']:
        reasons.append('Total loss limit reached')
    return reasons


def run(data_dir, out, epochs=12, payout=.8):
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = data_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'provider': 'User CSV, provenance unverified'}
    datasets = {s: load_candles(data_dir / f'{s}.csv') for s in ('EURUSD', 'USDJPY', 'AUDUSD')}
    snapshot = out/'data_snapshot'
    snapshot.mkdir(exist_ok=True)
    for asset in datasets:
        shutil.copy2(data_dir/f'{asset}.csv', snapshot/f'{asset}.csv')
    if manifest_path.exists():
        shutil.copy2(manifest_path, snapshot/'manifest.json')
    all_states = {s: features(rows) for s, rows in datasets.items()}
    start = max(r[0]['t'] for r in datasets.values())
    end = min(r[-1]['t'] for r in datasets.values()) + BAR
    # Common timestamp cutoffs across all assets, fixed before any fitting.
    train_end = int((start + (end-start)*.6)//BAR)*BAR
    valid_end = int((start + (end-start)*.8)//BAR)*BAR
    candidates, models = [], {}
    for asset, rows in datasets.items():
        states = all_states[asset]
        for minutes in (5, 10, 15):
            horizon = minutes//5
            print(f'Training {asset} / {minutes}m with 3 seeds', flush=True)
            trained = [train(rows, states, start, train_end, horizon, payout, s, epochs) for s in (101, 202, 303)]
            model = combine(trained)
            val = evaluate(rows, states, model, train_end, valid_end, horizon, payout)
            seed_results = [compact(evaluate(rows, states, m, train_end, valid_end, horizon, payout)) for m in trained]
            baselines = {p: compact(evaluate(rows, states, model, train_end, valid_end, horizon, payout, p))
                         for p in ('skip', 'up', 'down', 'momentum', 'random')}
            reasons = qualify(val, 10)
            if val['pnl'] <= max(x['pnl'] for x in baselines.values()):
                reasons.append('Did not beat every simple validation baseline')
            if any(x['pnl'] <= 0 for x in seed_results):
                reasons.append('Not profitable across all three training seeds')
            key = f'{asset}-{minutes}'
            score = val['daily_mean_ci95'][0] if val['daily_mean_ci95'][0] is not None else -1e9
            candidates.append({'key': key, 'asset': asset, 'expiry_minutes': minutes,
                               'validation': compact(val), 'seed_validation': seed_results,
                               'validation_baselines': baselines, 'rejection_reasons': reasons,
                               'validation_eligible': not reasons, 'selection_score': score})
            models[key] = model
    # Validation only selects the candidate. The final interval is accessed next.
    candidates.sort(key=lambda c: (c['validation_eligible'], c['selection_score'], c['validation']['pnl']), reverse=True)
    selected = candidates[0]
    asset, horizon = selected['asset'], selected['expiry_minutes']//5
    model = models[selected['key']]
    model.update({'asset': asset, 'expiry_minutes': horizon*5, 'payout_assumed': payout,
                  'training_end_exclusive': stamp(train_end), 'validation_end_exclusive': stamp(valid_end),
                  'algorithm': 'Tabular semi-Markov Q-learning, three-seed ensemble',
                  'epochs': epochs, 'seeds': [101, 202, 303], 'gamma_per_bar': .98,
                  'min_unique_support_per_seed': 30, 'q_advantage_threshold': .02})
    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()
    (out/'model.json').write_text(json.dumps(model, indent=2), encoding='utf-8')
    rows, states = datasets[asset], all_states[asset]
    test = evaluate(rows, states, model, valid_end, end, horizon, payout)
    baselines = {p: compact(evaluate(rows, states, model, valid_end, end, horizon, payout, p))
                 for p in ('skip', 'up', 'down', 'momentum', 'random')}
    stress = {f'payout_{p:.2f}': compact(evaluate(rows, states, model, valid_end, end, horizon, p)) for p in (.7, .8, .9)}
    stress['one_bar_delay'] = compact(evaluate(rows, states, model, valid_end, end, horizon, payout, delay=1))
    stress['one_percent_stake_cost'] = compact(evaluate(rows, states, model, valid_end, end, horizon, payout, cost=.01))
    reasons = list(selected['rejection_reasons']) + qualify(test, 20)
    if test['pnl'] <= max(x['pnl'] for x in baselines.values()):
        reasons.append('Did not beat every simple holdout baseline')
    if any(v['pnl'] <= 0 for v in stress.values()):
        reasons.append('At least one execution/payout stress did not show positive profit')
    reasons += ['External reference prices and assumed payouts are not Quotex execution validation',
                'No independent forward paper-trading record has been collected']
    report = {'created_at': stamp(datetime.now(timezone.utc).timestamp()),
              'status': 'RESEARCH ONLY / SKIP', 'execution_mode': 'Historical simulated execution only',
              'provider': manifest, 'data_hashes': {s: hashlib.sha256((data_dir/f'{s}.csv').read_bytes()).hexdigest() for s in datasets},
              'data_quality': {s: quality(r) for s,r in datasets.items()},
              'split': {'start': stamp(start), 'train_end': stamp(train_end), 'validation_end': stamp(valid_end), 'end_exclusive': stamp(end)},
              'assumptions': {'payout': payout, 'break_even_excluding_ties': 1/(1+payout), 'stake': 10,
                              'starting_balance': 1000, 'daily_loss_threshold': 30, 'total_loss_threshold': 100,
                              'bar_seconds': BAR, 'tie': 'stake refunded', 'overlapping_positions': False},
              'candidate_count': len(candidates), 'candidates': candidates, 'selected': selected,
              'model_sha256': model_hash, 'holdout': compact(test), 'holdout_baselines': baselines,
              'stress_tests': stress, 'promotion_blockers': list(dict.fromkeys(reasons)),
              'equity': [{'time': t['expiry_time'], 'balance': t['balance']} for t in test['ledger']],
              'daily': test['daily'], 'chart': rows[-120:],
              'learned_states': [{'state': s, 'action': ACTIONS[choose(model,s)], 'q': v,
                                  'support': model['support'][s]} for s,v in model['q'].items()],
              'last_observation': {'asset': asset, 'closed_at': stamp(rows[-1]['t']+BAR),
                                   'raw_model_action': ACTIONS[choose(model, states[-1])], 'permitted_action': 'SKIP'},
              'limitations': ['Selection across nine candidates can overfit validation. Bootstrap intervals are descriptive, not multiple-comparison-adjusted proof.',
                             'Daily bootstrap assumes independent days; market regimes and serial dependence can invalidate that assumption.',
                             'Five-minute OHLC cannot reproduce second-level fills, bid/ask spreads, broker ticks, or actual changing payouts.',
                             'Unchanged-price candles and rounded reference prices can substantially affect ties and short-expiry outcomes.',
                             'Fixed stake; one position; daily limit can overshoot by up to one stake. Total loss limit can likewise overshoot.',
                             'No economic-news filter. No real-time data subscription. No broker or account connection.',
                             'Q-values are discounted reward estimates, not calibrated win probabilities.',
                             'Do not repeatedly retune against this holdout. New versions need new unseen periods.']}
    (out/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    fields = ['decision_time','entry_time','expiry_time','direction','entry','exit','state','stake','payout_assumed','pnl','balance','result']
    with open(out/'paper_trades.csv','w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(test['ledger'])
    template = (ROOT/'dashboard.html').read_text(encoding='utf-8')
    (out/'dashboard.html').write_text(template.replace('__REPORT_JSON__',json.dumps(report).replace('<','\\u003c')),encoding='utf-8')
    print(json.dumps({'selected': selected['key'], 'holdout': compact(test), 'status': report['status'], 'report': str(out/'dashboard.html')},indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=ROOT/'data')
    parser.add_argument('--out', type=Path, default=ROOT/'results')
    parser.add_argument('--epochs', type=int, default=12)
    parser.add_argument('--payout', type=float, default=.8)
    args = parser.parse_args()
    if not 1 <= args.epochs <= 100 or not 0 < args.payout <= 1:
        parser.error('epochs must be 1..100 and payout a net return fraction in (0,1]')
    if (args.out/'report.json').exists():
        parser.error('Results already exist. Choose a new --out folder to preserve the frozen experiment.')
    run(args.data,args.out,args.epochs,args.payout)


if __name__ == '__main__':
    main()
