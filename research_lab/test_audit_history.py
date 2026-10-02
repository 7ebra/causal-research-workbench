import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from providers import oanda_research as research
from test_oanda_research import candle, START
from audit_history import audit


class HistoryAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        class Stop:
            def is_set(self): return False
            def wait(self, seconds): return False
        import io
        def fetch(url, token, timeout):
            instrument = url.split('/instruments/')[1].split('/')[0]
            return io.StringIO(json.dumps({'instrument': instrument, 'granularity': 'M5',
                'candles': [candle(START), candle(START+300)]}))
        research.history_worker({'token': 'TEST_ONLY'}, self.root, START, START+600, Stop(), fetch)

    def test_valid_export_reconciles(self):
        result = audit(self.root)
        self.assertEqual(result['integrity'], 'PASS')
        self.assertEqual(result['instruments']['EUR_USD']['candles_per_component'], 2)

    def test_partial_download_is_blocked(self):
        file = self.root/'history'/'manifest.json'
        manifest = json.loads(file.read_text()); manifest['state'] = 'PARTIAL'
        file.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'incomplete'): audit(self.root)

    def test_rehashed_corrupt_csv_still_fails_database_comparison(self):
        file = self.root/'history'/'EURUSD.csv'
        with file.open(newline='') as stream: rows = list(csv.DictReader(stream))
        rows[0]['close'] = '1.11'
        with file.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0]); writer.writeheader(); writer.writerows(rows)
        manifest_file = file.parent/'manifest.json'
        manifest = json.loads(manifest_file.read_text())
        manifest['data_hashes']['EUR_USD'] = hashlib.sha256(file.read_bytes()).hexdigest()
        manifest_file.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'disagreement'): audit(self.root)


if __name__ == '__main__': unittest.main()
