import json
import hashlib
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from pathlib import Path
import connectivity as c
import forward as f
from test_forward import models, raw, NOW


class ConnectivityTests(unittest.TestCase):
    def test_online_has_normal_cadence(self):
        state,event=c.advance(None,c.ASSETS,1000)
        self.assertEqual((state['state'],state['retry_seconds'],event),('ONLINE',60,'CONNECTION_ONLINE'))

    def test_outage_backs_off_to_five_minutes(self):
        state=None;delays=[]
        for i in range(8):
            state,event=c.advance(state,[],1000+i*60)
            delays.append(state['retry_seconds'])
        self.assertEqual(delays,[60,120,240,300,300,300,300,300])
        self.assertEqual(state['outage_since'],1000)

    def test_recovery_resets_backoff_and_logs_duration(self):
        state,_=c.advance(None,[],1000)
        state,event=c.advance(state,c.ASSETS,1240)
        self.assertEqual((state['state'],state['failure_streak'],state['retry_seconds']),('ONLINE',0,60))
        self.assertEqual(event,'CONNECTION_RECOVERED')
        self.assertEqual(state['recovery_count'],1)
        self.assertEqual(state['last_outage_seconds'],240)
        self.assertIsNone(state['outage_since'])

    def test_partial_feed_does_not_block_working_assets(self):
        state,event=c.advance(None,['EURUSD','USDJPY'],1000)
        self.assertEqual(state['state'],'DEGRADED')
        self.assertEqual(state['failed_assets'],['AUDUSD'])
        self.assertEqual(state['retry_seconds'],60)

    def test_same_outage_does_not_repeat_transition(self):
        state,_=c.advance(None,[],1000)
        state,event=c.advance(state,[],1060)
        self.assertIsNone(event)

    def test_persisted_outage_survives_restart(self):
        state,_=c.advance(None,[],1000)
        state,_=c.advance(json.loads(json.dumps(state)),[],1060)
        self.assertEqual(state['failure_streak'],2)
        self.assertEqual(state['outage_since'],1000)

    def test_duplicate_or_unknown_assets_do_not_fake_recovery(self):
        state,_=c.advance(None,['EURUSD','EURUSD','UNKNOWN'],1000)
        self.assertEqual(state['state'],'DEGRADED')
        self.assertEqual(len(state['failed_assets']),2)

    def test_outage_recovery_preserves_account_and_model_state(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor=f.PaperMonitor(Path(directory)/'test.sqlite',models(),NOW)
            try:
                before=monitor.meta('model_fingerprint')
                accepted=monitor.tick([],NOW)
                f.connection_step(monitor,accepted,[],NOW,60,'Network unreachable')
                self.assertEqual(monitor.summary(NOW)['connection']['state'],'RECONNECTING')
                self.assertEqual(monitor.db.execute('SELECT count(*) FROM trades').fetchone()[0],0)
                snapshots=[raw(a,now=NOW+60) for a in c.ASSETS]
                accepted=monitor.tick(snapshots,NOW+60)
                f.connection_step(monitor,accepted,snapshots,NOW+60,60)
                self.assertEqual(monitor.summary(NOW+60)['connection']['state'],'ONLINE')
                self.assertEqual(monitor.meta('model_fingerprint'),before)
                self.assertEqual(monitor.meta('started_at'),str(NOW))
                self.assertEqual(monitor.meta('ends_at'),str(NOW+86400))
                count=monitor.db.execute("SELECT count(*) FROM events WHERE kind='CONNECTION_RECOVERED'").fetchone()[0]
                self.assertEqual(count,1)
            finally:monitor.db.close()

    def test_network_backoff_keeps_status_alive_and_honours_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor=f.PaperMonitor(Path(directory)/'test.sqlite',models(),NOW)
            current=[NOW];published=[]
            try:
                with monitor.db:monitor.set_meta('ends_at',NOW+40)
                f.wait_for_retry(monitor,Path(directory),NOW+300,
                    clock=lambda:current[0],sleep=lambda seconds:current.__setitem__(0,current[0]+seconds),
                    publisher=lambda m,out,now:published.append(now))
                self.assertEqual(current[0],NOW+40)
                self.assertEqual(published,[NOW+15,NOW+30,NOW+40])
            finally:monitor.db.close()

    def test_stop_marker_interrupts_backoff(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor=f.PaperMonitor(Path(directory)/'test.sqlite',models(),NOW)
            try:
                (Path(directory)/'STOP').write_text('stop')
                f.wait_for_retry(monitor,Path(directory),NOW+300,clock=lambda:NOW,
                    sleep=lambda _:self.fail('Stop should not sleep'),publisher=lambda *args:None)
            finally:monitor.db.close()

    def test_real_worker_loop_survives_network_error_then_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);model_dir=root/'models';model_dir.mkdir();hashes={}
            for key,model in models().items():
                body=json.dumps(model).encode();(model_dir/f'{key}.json').write_bytes(body)
                hashes[key]=hashlib.sha256(body).hexdigest()
            (model_dir/'manifest.json').write_text(json.dumps({'hashes':hashes}))
            out=root/'pilot';current=[NOW];waits=[]
            def fake_wait(monitor,target,retry_at):
                waits.append(retry_at)
                if len(waits)==1:current[0]+=60
                else:(target/'STOP').write_text('Test completed')
            args=SimpleNamespace(out=out,models=model_dir,hours=24,node='unused',poll_seconds=60,once=False)
            with mock.patch.object(f,'collect',side_effect=[OSError('Network unreachable'),
                 [raw(a,now=NOW+60) for a in c.ASSETS]]),mock.patch.object(f.time,'time',side_effect=lambda:current[0]), \
                 mock.patch.object(f,'wait_for_retry',side_effect=fake_wait):
                f.run(args)
            db=sqlite3.connect(out/'experiment.sqlite')
            try:
                counts=dict(db.execute("SELECT kind,count(*) FROM events WHERE kind LIKE 'CONNECTION_%' GROUP BY kind"))
                self.assertEqual(counts['CONNECTION_LOST'],1)
                self.assertEqual(counts['CONNECTION_RECOVERED'],1)
                report=json.loads((out/'status.json').read_text())
                self.assertEqual(report['status'],'STOPPED')
                self.assertEqual(report['connection']['state'],'ONLINE')
                self.assertEqual(report['connection']['recovery_count'],1)
                self.assertEqual(report['started_at'],f.s.stamp(NOW))
                self.assertEqual(report['ends_at'],f.s.stamp(NOW+86400))
                self.assertEqual(len(waits),2)
            finally:db.close()


if __name__=='__main__':unittest.main()
