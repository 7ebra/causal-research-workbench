import copy
import hashlib
import json
import tempfile
import os
import subprocess
import sys
import unittest
from pathlib import Path
import forward as f
import screener as s

BASE=1704067200
NOW=BASE+100*s.BAR+20


def raw(asset='EURUSD',now=NOW,price=1.12):
    rows=[dict(t=BASE+i*s.BAR,open=1+i*.0001,high=1.0002+i*.0001,
               low=.9999+i*.0001,close=1.0001+i*.0001) for i in range(100)]
    return {'asset':asset,'received_at':now,'meta':{'regularMarketTime':int(now-1),'regularMarketPrice':price},
            'timestamps':[r['t'] for r in rows],
            'quote':{k:[r[k] for r in rows] for k in ('open','high','low','close')}}


def models():
    rows=f.normalize(raw())['bars']
    state=s.features(rows)[-1]
    return {f'{a}-{m}':{'asset':a,'expiry_minutes':m,'payout_assumed':.8,
            'q':{state:[0,1,0]},'support':{state:[100,100,100]},'research_only':True}
            for a in ('EURUSD','USDJPY','AUDUSD') for m in (5,10,15)}


class ForwardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'test.sqlite'
        self.m=f.PaperMonitor(self.path,models(),NOW)

    def tearDown(self):
        self.m.db.close();self.temp.cleanup()

    def account(self,key='EURUSD-5/rl'):
        return json.loads(self.m.db.execute('SELECT state FROM accounts WHERE key=?',(key,)).fetchone()[0])

    def test_bootstrap_does_not_trade_historical_bars(self):
        self.m.tick([raw()],NOW)
        rows=self.m.db.execute('SELECT entry_observed,decision_bar_t FROM trades').fetchall()
        self.assertTrue(rows)
        self.assertTrue(all(t==NOW and bar==BASE+99*s.BAR for t,bar in rows))
        self.assertEqual(self.account()['signals'],1)

    def test_duplicate_poll_never_duplicates_entries(self):
        self.m.tick([raw()],NOW)
        count=self.m.db.execute('SELECT count(*) FROM trades').fetchone()[0]
        self.m.tick([raw(now=NOW+10)],NOW+10)
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM trades').fetchone()[0],count)
        self.assertEqual(self.account()['signals'],1)

    def test_valid_settlement_uses_new_observed_quote(self):
        self.m.tick([raw()],NOW)
        self.m.tick([raw(now=NOW+301,price=1.13)],NOW+301)
        a=self.account();self.assertEqual(a['wins'],1);self.assertEqual(a['balance'],1008)
        row=self.m.db.execute("SELECT expiry_target,settled_observed,exit_quote_t,settlement_delay FROM trades WHERE account_key='EURUSD-5/rl'").fetchone()
        self.assertGreaterEqual(row[2],row[0]);self.assertEqual(row[3],1)

    def test_quote_before_expiry_cannot_settle(self):
        self.m.tick([raw()],NOW)
        snapshot=raw(now=NOW+300);snapshot['meta']['regularMarketTime']=int(NOW+299)
        self.m.tick([snapshot],NOW+300)
        self.assertIsNotNone(self.account()['pending']);self.assertEqual(self.account()['wins'],0)

    def test_missing_settlement_is_void_not_filled_later(self):
        self.m.tick([raw()],NOW)
        self.m.tick([],NOW+391)
        a=self.account();self.assertEqual(a['voids'],1);self.assertEqual(a['balance'],1000)
        self.assertIsNone(a['pending'])
        row=self.m.db.execute("SELECT status,pnl,exit FROM trades WHERE account_key='EURUSD-5/rl'").fetchone()
        self.assertEqual(row,('VOID_MISSING_SETTLEMENT',None,None))

    def test_stale_quote_blocks_entries(self):
        snapshot=raw();snapshot['meta']['regularMarketTime']=int(NOW-91)
        self.m.tick([snapshot],NOW)
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM trades').fetchone()[0],0)

    def test_malformed_metadata_is_logged_without_crashing_worker(self):
        snapshot=raw();snapshot['meta']=None
        self.m.tick([snapshot],NOW)
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM events WHERE kind='FEED_ERROR'").fetchone()[0],1)
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM trades').fetchone()[0],0)

    def test_malformed_snapshot_and_candle_objects_do_not_crash_worker(self):
        malformed=raw();malformed['quote']=None
        self.m.tick([None,malformed],NOW)
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM events WHERE kind='FEED_ERROR'").fetchone()[0],2)

    def test_low_activity_warning_reports_abstention_without_forcing_entries(self):
        for i in range(100):
            self.m.event(NOW,'EURUSD-5/rl','DECISION',{'reason':'MODEL_SKIP',
                'policy_diagnostic':{'reason':'Q_PREFERS_SKIP'}})
        summary=self.m.summary(NOW)
        self.assertEqual(summary['rl_decision_reasons'],{'Q_PREFERS_SKIP':100})
        self.assertIn('activity is low',summary['rl_activity_warning'])
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM trades').fetchone()[0],0)

    def test_decision_records_policy_and_execution_gate_separately(self):
        snapshot=raw();snapshot['meta']['regularMarketTime']=int(NOW-91)
        self.m.tick([snapshot],NOW)
        details=json.loads(self.m.db.execute("SELECT details FROM events WHERE kind='DECISION' AND asset='EURUSD-5/rl'").fetchone()[0])
        self.assertEqual(details['reason'],'STALE_QUOTE')
        self.assertEqual(details['policy_diagnostic']['reason'],'MODEL_WANTS_TRADE')
        self.assertEqual(details['policy_diagnostic']['action'],1)
        self.assertEqual(self.m.summary(NOW)['rl_decision_reasons'],{'STALE_QUOTE':3})

    def test_processing_delay_is_included_in_entry_freshness(self):
        snapshot=raw();snapshot['meta']['regularMarketTime']=int(NOW-80)
        self.m.tick([snapshot],NOW+30)
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM trades').fetchone()[0],0)

    def test_future_quote_is_rejected(self):
        snapshot=raw();snapshot['meta']['regularMarketTime']=NOW+6
        with self.assertRaisesRegex(ValueError,'Invalid reference'):f.normalize(snapshot)

    def test_unclosed_and_irregular_rows_are_excluded(self):
        snapshot=raw();snapshot['timestamps'].extend([BASE+100*s.BAR,int(NOW-1)])
        for k in snapshot['quote']:snapshot['quote'][k].extend([1.1,1.1])
        self.assertEqual(len(f.normalize(snapshot)['bars']),100)

    def test_invalid_ohlc_is_excluded(self):
        snapshot=raw();snapshot['quote']['high'][70]=.5
        q=f.normalize(snapshot);self.assertEqual(q['invalid_bars'],1)
        self.assertEqual(len(q['bars']),99)

    def test_gap_blocks_feature_and_entry(self):
        snapshot=raw()
        del snapshot['timestamps'][90]
        for values in snapshot['quote'].values():del values[90]
        self.m.tick([snapshot],NOW)
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM trades').fetchone()[0],0)

    def test_revisions_preserve_first_observed_bar(self):
        self.m.tick([raw()],NOW)
        snapshot=raw(now=NOW+10);snapshot['quote']['close'][80]+=0.00001
        self.m.tick([snapshot],NOW+10)
        observed=self.m.db.execute('SELECT c FROM bars WHERE asset=? AND t=?',('EURUSD',BASE+80*s.BAR)).fetchone()[0]
        self.assertEqual(observed,raw()['quote']['close'][80])

    def test_restart_preserves_pending_and_deduplicates(self):
        self.m.tick([raw()],NOW);pending=self.account()['pending']['id']
        self.m.db.close();self.m=f.PaperMonitor(self.path,models(),NOW+30)
        self.m.tick([raw(now=NOW+30)],NOW+30)
        self.assertEqual(self.account()['pending']['id'],pending)
        self.assertEqual(self.account()['signals'],1)

    def test_changed_models_cannot_resume_experiment(self):
        changed=models();changed['EURUSD-5']['payout_assumed']=.9
        with self.assertRaisesRegex(ValueError,'differ'):f.PaperMonitor(self.path,changed,NOW+30)

    def test_risk_stop_blocks_new_entry(self):
        a=self.account();a['balance']=900
        with self.m.db:self.m.db.execute('UPDATE accounts SET state=? WHERE key=?',(json.dumps(a),'EURUSD-5/rl'))
        self.m.tick([raw()],NOW)
        self.assertIsNone(self.account()['pending']);self.assertEqual(self.account()['skips'],1)

    def test_daily_loss_stop_blocks_new_entry(self):
        a=self.account();a['daily'][s.stamp(NOW)[:10]]=-30
        with self.m.db:self.m.db.execute('UPDATE accounts SET state=? WHERE key=?',(json.dumps(a),'EURUSD-5/rl'))
        self.m.tick([raw()],NOW);self.assertIsNone(self.account()['pending'])

    def test_run_end_and_stopping_void_pending(self):
        self.m.tick([raw()],NOW);self.m.finish(NOW+10,'STOPPED')
        self.assertEqual(self.m.meta('status'),'STOPPED')
        self.assertIsNone(self.account()['pending']);self.assertEqual(self.account()['voids'],1)

    def test_collector_clock_mismatch_blocks_entries(self):
        self.m.tick([raw()],NOW+61)
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM trades').fetchone()[0],0)
        self.assertEqual(self.m.summary(NOW+61)['feed_errors'],1)

    def test_model_file_tampering_is_rejected(self):
        directory=Path(self.temp.name)/'models';directory.mkdir();hashes={}
        for key,model in models().items():
            body=json.dumps(model).encode();(directory/f'{key}.json').write_bytes(body)
            hashes[key]=hashlib.sha256(body).hexdigest()
        (directory/'manifest.json').write_text(json.dumps({'hashes':hashes}))
        self.assertEqual(len(f.verified_models(directory)[0]),9)
        (directory/'EURUSD-5.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'hash changed'):f.verified_models(directory)

    def test_worker_lock_prevents_a_second_process(self):
        directory=Path(self.temp.name)/'locked';directory.mkdir()
        handle=(directory/'worker.lock').open('w+b');handle.write(b'0');handle.flush();handle.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            result=subprocess.run([sys.executable,str(Path(f.__file__)), '--once','--out',str(directory)],
                                  capture_output=True,text=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((directory/'experiment.sqlite').exists())
        finally:handle.close()

    def test_entry_near_deadline_is_blocked(self):
        with self.m.db:self.m.set_meta('ends_at',NOW+60)
        self.m.tick([raw()],NOW)
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM trades').fetchone()[0],0)


if __name__=='__main__':unittest.main()
