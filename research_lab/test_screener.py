import csv
import random
import tempfile
import unittest
from pathlib import Path
import screener as s


def fixture(n=1500):
    rng=random.Random(23);rows=[];price=1.1
    for i in range(n):
        close=price+rng.choice([-.0002,-.0001,.0001,.0002])
        rows.append({'t': 1704067200+i*s.BAR,'open':price,'close':close,
                     'high':max(price,close)+.0001,'low':min(price,close)-.0001})
        price=close
    return rows


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.rows=fixture();self.states=s.features(self.rows)

    def test_features_do_not_see_future(self):
        altered=[dict(r) for r in self.rows]
        for r in altered[800:]:
            for k in ('open','high','low','close'):r[k]*=10
        self.assertEqual(self.states[:800],s.features(altered)[:800])
        self.assertEqual(self.states[:800],s.features(self.rows[:800]))

    def test_expiry_never_crosses_partition(self):
        start,end=self.rows[400]['t'],self.rows[800]['t']
        ops=s.opportunities(self.rows,self.states,start,end,3)
        self.assertTrue(ops)
        for i,(entry,expiry) in ops.items():
            self.assertGreaterEqual(self.rows[i]['t']+s.BAR,start)
            self.assertGreaterEqual(self.rows[entry]['t'],start)
            self.assertLess(self.rows[expiry]['t'],end)
            self.assertEqual(expiry-entry,3)

    def test_gap_resets_warmup_and_blocks_crossing(self):
        rows=self.rows[:500]+self.rows[501:]
        states=s.features(rows)
        self.assertTrue(all(x is None for x in states[500:559]))
        for i,(e,x) in s.opportunities(rows,states,0,10**12,3).items():
            self.assertEqual(rows[x]['t']-rows[i]['t'],(x-i)*s.BAR)

    def test_payoffs_and_ties(self):
        self.assertEqual(s.payoff(1,2,1,.8),.8)
        self.assertEqual(s.payoff(1,2,2,.8),-1)
        self.assertEqual(s.payoff(1,1,2,.8),0)
        self.assertEqual(s.payoff(1,2,0,.8),0)
        self.assertAlmostEqual(s.payoff(1,2,1,.8,.01),.79)

    def test_delay_preserves_expiry_duration(self):
        ops=s.opportunities(self.rows,self.states,0,10**12,3,delay=1)
        for i,(e,x) in ops.items():
            self.assertEqual(e,i+2);self.assertEqual(x,e+3)

    def test_deterministic_training(self):
        args=(self.rows,self.states,0,self.rows[1000]['t'],2,.8,42,2)
        self.assertEqual(s.train(*args),s.train(*args))

    def test_training_never_uses_holdout_prices(self):
        original=s.train(self.rows,self.states,0,self.rows[1000]['t'],2,.8,42,2)
        rows=[dict(r) for r in self.rows]
        for r in rows[1000:]:r['open']*=2
        changed=s.train(rows,self.states,0,rows[1000]['t'],2,.8,42,2)
        self.assertEqual(original,changed)

    def test_repeated_epochs_do_not_inflate_support(self):
        model=s.train(self.rows,self.states,0,self.rows[500]['t'],1,.8,42,5)
        available={}
        for i in s.opportunities(self.rows,self.states,0,self.rows[500]['t'],1):
            available[self.states[i]]=available.get(self.states[i],0)+1
        for state,counts in model['support'].items():
            self.assertTrue(all(c<=available[state] for c in counts))

    def test_no_overlapping_positions(self):
        r=s.evaluate(self.rows,self.states,{},0,10**12,3,policy='up')
        for a,b in zip(r['ledger'],r['ledger'][1:]):
            self.assertGreaterEqual(b['entry_time'],a['expiry_time'])

    def test_loss_caps_and_fixed_stake(self):
        rows=[{'t':1704067200+i*s.BAR,'open':1+i*.0001,'close':1+(i+1)*.0001,
               'low':1+i*.0001,'high':1+(i+1)*.0001} for i in range(1500)]
        r=s.evaluate(rows,s.features(rows),{},0,10**12,1,policy='down')
        self.assertEqual(r['ending_balance'],900)
        self.assertEqual(r['trades'],10)
        self.assertTrue(r['total_loss_stop_reached'])
        self.assertTrue(all(t['stake']==10 for t in r['ledger']))
        self.assertTrue(all(x>=-30 for x in r['daily'].values()))

    def test_unknown_state_skips(self):
        self.assertEqual(s.choose({'q':{},'support':{}},'unknown'),0)

    def test_low_support_skips(self):
        self.assertEqual(s.choose({'q':{'a':[0,1,0]},'support':{'a':[100,29,100]}},'a'),0)

    def test_policy_diagnostics_preserve_original_choices(self):
        import itertools
        for q in itertools.product((-.2, 0, .01, .02, .5), repeat=3):
            for support in (29,30,100):
                model={'q':{'a':list(q)},'support':{'a':[support]*3}}
                original=max(range(3),key=lambda a:q[a])
                if original and (q[original]<=q[0]+.02 or support<30):original=0
                self.assertEqual(s.choose(model,'a'),original)

    def test_policy_diagnostics_distinguish_skip_causes(self):
        cases=[([1,0,0],100,'Q_PREFERS_SKIP'),([0,.01,0],100,'INSUFFICIENT_Q_ADVANTAGE'),
               ([0,1,0],29,'LOW_SUPPORT'),([0,1,0],30,'MODEL_WANTS_TRADE')]
        for q,support,reason in cases:
            detail=s.explain_choice({'q':{'a':q},'support':{'a':[support]*3}},'a')
            self.assertEqual(detail['reason'],reason)
        self.assertEqual(s.explain_choice({'q':{},'support':{}},None)['reason'],'GAP_OR_WARMUP')
        self.assertEqual(s.explain_choice({'q':{},'support':{}},'a')['reason'],'UNSEEN_STATE')

    def test_no_trade_is_zero_profit(self):
        r=s.evaluate(self.rows,self.states,{},0,10**12,1,policy='skip')
        self.assertEqual((r['trades'],r['pnl'],r['max_drawdown']),(0,0,0))
        self.assertTrue(s.qualify(r,20))

    def test_duplicate_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.csv'
            p.write_text('timestamp,open,high,low,close\n1704067200,1,2,1,2\n1704067200,1,2,1,2\n')
            with self.assertRaisesRegex(ValueError,'unique'):s.load_candles(p)


if __name__=='__main__':unittest.main()
