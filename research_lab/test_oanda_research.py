import io
import json
import tempfile
from pathlib import Path
import threading
import unittest
from unittest import mock
from urllib.request import Request
from providers import oanda_practice as p
from providers import oanda_research as r


START=1790726400  # Aligned UTC midnight.


def candle(t,complete=True):
    return {'time':r.utc(t),'complete':complete,'volume':3,
            'mid':{'o':'1.1','h':'1.2','l':'1.0','c':'1.15'},
            'bid':{'o':'1.099','h':'1.199','l':'0.999','c':'1.149'},
            'ask':{'o':'1.101','h':'1.201','l':'1.001','c':'1.151'}}


class ResearchTests(unittest.TestCase):
    def test_incomplete_and_end_boundary_candles_are_excluded(self):
        self.assertIsNone(r.normalize_candle(candle(START,False),START,START+300))
        self.assertIsNone(r.normalize_candle(candle(START+300),START,START+300))
        self.assertIsNotNone(r.normalize_candle(candle(START),START,START+300))

    def test_invalid_ohlc_and_alignment_are_rejected(self):
        data=candle(START);data['bid']['h']='0.5'
        with self.assertRaises(ValueError):r.normalize_candle(data,START,START+300)
        with self.assertRaises(ValueError):r.normalize_candle(candle(START+1),START,START+600)

    def test_page_windows_do_not_skip_or_overlap_ranges(self):
        parts=list(r.windows(START,START+10*86400))
        self.assertEqual(parts[0][0],START)
        self.assertEqual(parts[-1][1],START+10*86400)
        self.assertTrue(all(a[1]==b[0] for a,b in zip(parts,parts[1:])))

    def test_only_price_and_candle_endpoints_are_allowed(self):
        with mock.patch.object(p,'build_opener'):
            with self.assertRaises(ValueError):p.open_readonly(p.API+'/v3/accounts/test/orders','dummy')
            with self.assertRaises(ValueError):p.open_readonly(p.API+'/v3/instruments/UNKNOWN/candles','dummy')
            p.open_readonly(p.API+'/v3/instruments/CAD_JPY/candles?price=MBA','dummy')

    def test_history_exports_only_complete_requested_candles(self):
        def fetch(url,token,timeout):
            instrument=url.split('/instruments/')[1].split('/')[0]
            payload={'instrument':instrument,'granularity':'M5',
                     'candles':[candle(START),candle(START+300,False),candle(START+600)]}
            return io.StringIO(json.dumps(payload))
        stop=mock.Mock();stop.is_set.return_value=False;stop.wait.return_value=False
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            r.history_worker({'token':'TEST_ONLY'},out,START,START+600,stop,fetch)
            manifest=json.loads((out/'history'/'manifest.json').read_text())
            self.assertEqual(manifest['state'],'COMPLETED')
            self.assertTrue(all(x['mid_candles']==1 for x in manifest['instruments'].values()))
            lines=(out/'history'/'EURUSD.csv').read_text().splitlines()
            self.assertEqual(len(lines),2)

    def test_clock_probe_is_read_only_and_retains_samples(self):
        response=mock.Mock(stdout='18:09:21, +00.3015513s\n18:09:23, +00.3076311s\n',returncode=0)
        with tempfile.TemporaryDirectory() as directory,mock.patch.object(r.subprocess,'run',return_value=response) as command:
            result=r.clock_probe(Path(directory))
            self.assertEqual(result['status'],'MEASURED')
            self.assertFalse(result['system_clock_changed'])
            self.assertNotIn('/resync',command.call_args.args[0])
            self.assertAlmostEqual(result['reference_ahead_of_local_median_seconds'],.3045912)

    def test_launcher_token_is_only_in_private_pipe(self):
        config={'account':'101-001-1234567-001','token':'NOT_A_REAL_TOKEN_123456789'}
        child=mock.Mock(pid=456)
        with tempfile.TemporaryDirectory() as directory,mock.patch.object(p,'check'),mock.patch.object(r.subprocess,'Popen',return_value=child) as launch,mock.patch('builtins.print'):
            out=Path(directory)/'study';r.launch(out,24,90,config)
            self.assertNotIn(config['token'],' '.join(launch.call_args.args[0]))
            self.assertEqual(json.loads(child.stdin.write.call_args.args[0]),config)
            self.assertTrue(all(config['token'].encode() not in file.read_bytes() for file in out.iterdir()))


if __name__=='__main__':unittest.main()
