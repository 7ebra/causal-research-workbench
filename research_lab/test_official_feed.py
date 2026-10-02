import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.request import Request
from providers import oanda_practice as p


def sample():
    return {'type':'PRICE','instrument':'EUR_USD','time':'2026-09-30T15:00:00.123456789Z',
            'bids':[{'price':'1.10000'}],'asks':[{'price':'1.10020'}],'status':'tradeable'}


class OfficialFeedTests(unittest.TestCase):
    def test_nanosecond_timestamp_is_preserved(self):
        self.assertEqual(p.epoch_ns(sample()['time'])%1_000_000_000,123456789)

    def test_bid_ask_mid_and_age(self):
        source=p.epoch_ns(sample()['time']);quote=p.normalize(sample(),source+250_000_000)
        self.assertAlmostEqual(quote['mid'],1.1001)
        self.assertAlmostEqual(quote['spread'],.0002)
        self.assertEqual(quote['age_seconds'],.25)

    def test_heartbeat_is_not_a_price(self):
        self.assertIsNone(p.normalize({'type':'HEARTBEAT'},0))

    def test_crossed_quote_is_rejected(self):
        data=sample();data['asks'][0]['price']='1.09'
        with self.assertRaises(ValueError):p.normalize(data,p.epoch_ns(data['time']))

    def test_nonfinite_price_is_rejected(self):
        data=sample();data['bids'][0]['price']='nan'
        with self.assertRaises(ValueError):p.normalize(data,p.epoch_ns(data['time']))

    def test_malformed_message_and_token_cannot_be_forwarded(self):
        with self.assertRaises(ValueError):p.normalize([],0)
        with self.assertRaises(ValueError):p.validate_credentials('101-001-1234567-001','X'*30+'\r\nInjected')

    def test_live_hosts_and_other_destinations_are_blocked_before_network(self):
        for url in ('https://api-fxtrade.oanda.com/v3/accounts/x/pricing',
                    'http://api-fxpractice.oanda.com/v3/accounts/x/pricing',
                    'https://example.com/v3/accounts/x/pricing',
                    'https://api-fxpractice.oanda.com:8443/v3/accounts/x/pricing'):
            with self.assertRaises(ValueError):p.open_readonly(url,'dummy')

    def test_redirects_cannot_forward_credentials(self):
        with self.assertRaises(HTTPError):
            p.NoRedirect().redirect_request(Request(p.API+'/v3/accounts/test/pricing'),None,302,'Moved',{},'https://other.example')

    def test_request_is_get_on_practice_host(self):
        fake=mock.Mock();fake.open.return_value='response'
        with mock.patch.object(p,'build_opener',return_value=fake):
            p.open_readonly(p.API+'/v3/accounts/test/pricing','UNIT_TEST_TOKEN')
        request=fake.open.call_args.args[0]
        self.assertEqual(request.get_method(),'GET')
        self.assertEqual(request.host,'api-fxpractice.oanda.com')

    def test_access_check_requires_all_requested_instruments(self):
        response=mock.MagicMock()
        response.__enter__.return_value=__import__('io').StringIO(json.dumps({'prices':[sample()]}))
        with mock.patch.object(p,'open_readonly',return_value=response):
            with self.assertRaisesRegex(ValueError,'every requested'):p.check({'account':'test','token':'TEST_ONLY'})

    def test_errors_do_not_echo_secrets(self):
        secret='PRIVATE_TEST_TOKEN'
        error=HTTPError('https://example.test/'+secret,401,secret,{},None)
        self.assertEqual(p.error_label(error),'AUTHORIZATION_REQUIRED')
        self.assertNotIn(secret,p.error_label(error))
        self.assertEqual(p.error_label(URLError(secret)),'NETWORK_OR_TLS_ERROR')

    def test_noninteractive_prompt_is_refused_before_secret_entry(self):
        with mock.patch.object(p.sys.stdin,'isatty',return_value=False),mock.patch.object(p.getpass,'getpass') as prompt:
            with self.assertRaises(RuntimeError):p.credentials()
            prompt.assert_not_called()

    def test_launcher_keeps_token_out_of_arguments_and_files(self):
        config={'account':'101-001-1234567-001','token':'NOT_A_REAL_API_TOKEN_123456789'}
        child=mock.Mock();child.pid=123;child.stdin=mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/'readiness'
            with mock.patch.object(p,'check'),mock.patch.object(p.subprocess,'Popen',return_value=child) as start:
                p.launch(out,15,config)
            command=start.call_args.args[0]
            self.assertNotIn(config['token'],' '.join(command))
            self.assertNotIn(config['account'],' '.join(command))
            for file in out.iterdir():self.assertNotIn(config['token'].encode(),file.read_bytes())
            payload=json.loads(child.stdin.write.call_args.args[0])
            self.assertEqual(payload,config)
            child.stdin.close.assert_called_once()


if __name__=='__main__':unittest.main()
