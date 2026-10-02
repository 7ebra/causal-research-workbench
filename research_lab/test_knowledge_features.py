import unittest
from test_screener import fixture
import knowledge_features as k


class KnowledgeFeatureTests(unittest.TestCase):
    def test_prefix_is_unchanged_when_future_prices_change(self):
        rows=fixture(300); original=k.features(rows)
        changed=[dict(r) for r in rows]
        for row in changed[150:]:
            for name in ('open','high','low','close'):row[name]*=10
        self.assertEqual(original[:150],k.features(changed)[:150])
        self.assertEqual(original[:150],k.features(rows[:150]))

    def test_gap_requires_fresh_contiguous_warmup(self):
        rows=fixture(200); del rows[100]
        states=k.features(rows)
        self.assertTrue(all(s is None for s in states[100:159]))
        self.assertIsNotNone(states[159])

    def test_flat_prices_are_neutral_without_division_by_zero(self):
        rows=[dict(t=1704067200+i*300,open=1.,high=1.,low=1.,close=1.) for i in range(60)]
        self.assertEqual(k.features(rows)[-1],'0,0,0')

    def test_monotonic_trend_has_expected_feature_direction(self):
        rows=[dict(t=1704067200+i*300,open=1.+i*.001,high=1.001+i*.001,
                   low=.999+i*.001,close=1.+i*.001) for i in range(80)]
        self.assertTrue(k.features(rows)[-1].startswith('1,1,'))

    def test_duplicate_time_and_bad_ohlc_are_rejected(self):
        rows=fixture(60); rows[-1]['t']=rows[-2]['t']
        with self.assertRaises(ValueError):k.features(rows)
        rows=fixture(60); rows[-1]['high']=rows[-1]['low']-.01
        with self.assertRaises(ValueError):k.features(rows)


if __name__=='__main__':unittest.main()
