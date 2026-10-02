"""Exercise the real collector retry loop with local failure injection, no network."""
from contextlib import ExitStack
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from providers import oanda_research as r


class Clock:
    def __init__(self): self.elapsed=0.; self.base=1790780400.
    def monotonic(self): return self.elapsed
    def time(self): return self.base+self.elapsed
    def time_ns(self): return int(self.time()*1e9)
    def monotonic_ns(self): return int(self.elapsed*1e9)
    def sleep(self,seconds): self.elapsed+=seconds


class Stream:
    def __init__(self,clock,drop=False): self.clock=clock; self.drop=drop; self.reads=0
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def readline(self):
        self.reads+=1; self.clock.sleep(1 if self.reads==1 else 5)
        if self.drop and self.reads>1: raise TimeoutError('Injected test outage')
        message={'type':'HEARTBEAT','time':r.utc(self.clock.time())}
        if self.reads==1:
            message.update(type='PRICE',instrument='EUR_USD',bids=[{'price':'1.1'}],
                           asks=[{'price':'1.1002'}],status='tradeable')
        return (json.dumps(message)+'\n').encode()


class ReconnectTests(unittest.TestCase):
    def run_collector(self,fetch,seconds=36,clock=None):
        clock=clock or Clock()
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            out=Path(directory)
            for name in ('time','time_ns','monotonic','monotonic_ns','sleep'):
                stack.enter_context(mock.patch.object(r.time,name,getattr(clock,name)))
            stack.enter_context(mock.patch.object(r.threading,'Thread'))
            stack.enter_context(mock.patch.object(r.p,'open_readonly',side_effect=fetch))
            r.record(out,seconds/3600,90,{'account':'TEST_ACCOUNT','token':'TEST_ONLY'})
            status=json.loads((out/'status.json').read_text())
            db=sqlite3.connect(out/'prices.sqlite')
            result={'status':status,'prices':db.execute('SELECT count(*) FROM prices').fetchone()[0],
                    'events':db.execute('SELECT kind FROM events').fetchall(),
                    'deadline':db.execute('SELECT ends-started FROM run').fetchone()[0]}
            db.close()
            self.assertTrue(all('TEST_ONLY' not in f.read_text() for f in out.glob('*.json')))
            return result

    def test_timeout_reconnect_preserves_quotes_and_original_deadline(self):
        clock=Clock(); calls=[]
        def fetch(url,token,timeout):
            self.assertTrue(url.startswith(r.p.STREAM+'/v3/accounts/'))
            self.assertEqual(timeout,15); calls.append(clock.elapsed)
            if len(calls)==1: return Stream(clock,drop=True)
            if len(calls)==2: raise URLError('Injected disconnected internet')
            return Stream(clock)
        result=self.run_collector(fetch,clock=clock)
        self.assertEqual(result['status']['state'],'COMPLETED')
        self.assertEqual(result['status']['errors'],2)
        self.assertEqual(result['status']['recoveries'],1)
        self.assertEqual(result['prices'],2)
        self.assertEqual(result['deadline'],36)
        self.assertIn(('RECONNECTING',),result['events'])

    def test_extended_outage_backs_off_and_still_finishes_at_deadline(self):
        clock=Clock(); calls=[]
        def fetch(*args,**kwargs):
            calls.append(clock.elapsed); raise URLError('Injected outage')
        result=self.run_collector(fetch,seconds=130,clock=clock)
        self.assertEqual(calls,[0,2,6,14,30,62,122])
        self.assertEqual(clock.elapsed,130)
        self.assertEqual(result['status']['state'],'COMPLETED')
        self.assertEqual(result['prices'],0)
        self.assertEqual(result['status']['recoveries'],0)

    def test_invalid_credentials_stop_without_endless_network_retry(self):
        fetch=mock.Mock(side_effect=HTTPError('https://example.test',401,'TEST_SECRET',{},None))
        result=self.run_collector(fetch)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(result['status']['state'],'ACCESS_REQUIRED')
        self.assertEqual(result['status']['recoveries'],0)


if __name__=='__main__': unittest.main()
