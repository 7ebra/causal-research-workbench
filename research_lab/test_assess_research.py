import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
import assess_research as a
import audit_history


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'status.json').write_text(json.dumps({'state':'COMPLETED','errors':0,'recoveries':0}))
        (self.root/'clock-probes.jsonl').write_text('')
        db=sqlite3.connect(self.root/'prices.sqlite')
        db.executescript('CREATE TABLE run(started REAL,ends REAL,started_monotonic REAL);'
            'CREATE TABLE prices(instrument TEXT,source_ns INTEGER,received_ns INTEGER,receipt_monotonic_ns INTEGER,bid REAL,ask REAL,age_seconds REAL);'
            'CREATE TABLE messages(received_ns INTEGER,receipt_monotonic_ns INTEGER,kind TEXT);'
            'CREATE TABLE events(observed_ns INTEGER,kind TEXT,details TEXT);')
        db.execute('INSERT INTO run VALUES(?,?,?)',(1704067200,1704067800,100))
        for asset in audit_history.ASSETS:
            for i in range(100):
                source=1704067200000000000+i*1000000000
                db.execute('INSERT INTO prices VALUES(?,?,?,?,?,?,?)',(asset,source,source+100000000,100000000000+i*1000000000,1.,1.0002,.1))
        db.commit();db.close()

    def test_completed_integrity_pass_allows_only_historical_diagnostics(self):
        with mock.patch.object(a.audit_history,'audit',return_value={'integrity':'PASS'}):result=a.assess(self.root)
        self.assertTrue(result['historical_fitting_allowed'])
        self.assertFalse(result['strategy_approved'])
        self.assertFalse(result['orders_enabled'])
        self.assertEqual(result['instruments']['EUR_USD']['quotes_inside_monotonic_window'],100)

    def test_corrupted_bid_ask_blocks_fitting(self):
        db=sqlite3.connect(self.root/'prices.sqlite')
        db.execute("UPDATE prices SET ask=.9 WHERE instrument='EUR_USD'");db.commit();db.close()
        with mock.patch.object(a.audit_history,'audit',return_value={'integrity':'PASS'}):result=a.assess(self.root)
        self.assertFalse(result['historical_fitting_allowed'])

    def test_running_collection_cannot_be_finalized(self):
        (self.root/'status.json').write_text('{"state":"ONLINE"}')
        with self.assertRaisesRegex(ValueError,'not completed'):a.assess(self.root)


if __name__=='__main__':unittest.main()
