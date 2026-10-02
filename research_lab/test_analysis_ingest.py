import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import URLError
import analysis_ingest as a


def response(seen=1790880000,publication='2026-10-01T18:00:00Z',text='USD/JPY may fall if support breaks.'):
    meta={'@type':'NewsArticle','headline':'USD/JPY chart analysis','datePublished':publication,
          'author':[{'name':'Analyst'}]}
    body='<script type="application/ld+json">'+json.dumps(meta)+'</script><div class="rich-text"><p>'+text+'</p></div>'
    return {'url':a.HOME+'markets/test/','received_at':seen,'body':body,'response_sha256':hashlib.sha256(body.encode()).hexdigest(),
            'headers':{'Date':'Thu, 01 Oct 2026 20:00:00 GMT'}}


class AnalysisIngestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=a.database(Path(self.temp.name)/'test.sqlite');self.addCleanup(self.db.close)

    def test_old_publication_remains_archive_despite_fresh_http_date(self):
        a.capture(self.db,response(publication='2026-09-01T18:00:00Z'))
        reason=self.db.execute('SELECT eligibility FROM articles').fetchone()[0]
        self.assertEqual(reason,'ARCHIVE_CONTEXT')

    def test_revision_retains_original_content_and_first_observation(self):
        self.assertEqual(a.capture(self.db,response()),'FIRST_SEEN')
        first=self.db.execute('SELECT first_seen,content_hash FROM articles').fetchone()
        self.assertEqual(a.capture(self.db,response(seen=1790880060,text='USD/JPY could rise unless resistance holds.')),'REVISION')
        self.assertEqual(self.db.execute('SELECT first_seen,content_hash FROM articles ORDER BY id LIMIT 1').fetchone(),first)
        self.assertEqual(self.db.execute('SELECT count(*) FROM articles').fetchone()[0],2)

    def test_conditional_commentary_never_becomes_flat_directional_call(self):
        a.capture(self.db,response())
        context=json.loads(self.db.execute('SELECT context FROM articles').fetchone()[0])
        self.assertTrue(context['conditional_language']);self.assertIsNone(context['forecast_direction'])
        self.assertTrue(context['needs_semantic_review'])

    def test_repeated_capture_deduplicates_without_backdating(self):
        a.capture(self.db,response());self.assertEqual(a.capture(self.db,response(seen=1790880010)),'UNCHANGED')
        row=self.db.execute('SELECT first_seen,last_seen FROM articles').fetchone()
        self.assertEqual(row,(1790880000,1790880010))

    def test_naive_publication_and_unapproved_host_are_rejected(self):
        with self.assertRaises(ValueError):a.capture(self.db,response(publication='2026-10-01T18:00:00'))
        r=response();r['url']='https://www.marketpulse.com.example.test/article'
        with self.assertRaises(ValueError):a.capture(self.db,r)

    def test_self_closing_images_do_not_drop_analysis_paragraphs(self):
        r=response(text='USD/JPY bullish <img src="x"/> but may reverse if support breaks.')
        text=a.parsed_article(r['body'])['text']
        self.assertIn('may reverse if support breaks',text)

    def test_initial_network_failure_recovers_without_resetting_deadline(self):
        clock=[0.];calls=[]
        def fake_sleep(seconds):clock[0]+=seconds
        def fake_fetch(url):
            calls.append((url,clock[0]))
            if len(calls)==1:raise URLError('Injected outage')
            if url.endswith('robots.txt'):body='User-agent: *\nAllow: /'
            elif url==a.HOME:body='<a href="/markets/test/">USD/JPY chart analysis</a>'
            else:body=response()['body']
            return dict(response(seen=1790880000+clock[0]),url=url,body=body)
        out=Path(self.temp.name)/'collector'
        with mock.patch.object(a,'fetch',side_effect=fake_fetch),mock.patch.object(a.time,'monotonic',side_effect=lambda:clock[0]),mock.patch.object(a.time,'time',side_effect=lambda:1790880000+clock[0]),mock.patch.object(a.time,'sleep',side_effect=fake_sleep),mock.patch('builtins.print'):
            a.collect(out,minutes=1,poll_seconds=900)
        state=json.loads((out/'status.json').read_text())
        self.assertEqual(state['state'],'COMPLETED')
        self.assertEqual(state['fetch_errors'],1)
        self.assertEqual(state['article_versions'],1)
        self.assertEqual(state['elapsed_monotonic_seconds'],60)
        self.assertEqual(calls[1][1],2)

    def test_permanent_outage_stops_at_fetch_budget(self):
        clock=[0.]
        def sleep(seconds):clock[0]+=seconds
        out=Path(self.temp.name)/'outage'
        with mock.patch.object(a,'fetch',side_effect=URLError('Injected outage')),mock.patch.object(a.time,'monotonic',side_effect=lambda:clock[0]),mock.patch.object(a.time,'time',side_effect=lambda:1790880000+clock[0]),mock.patch.object(a.time,'sleep',side_effect=sleep),mock.patch('builtins.print'):
            a.collect(out,minutes=90)
        state=json.loads((out/'status.json').read_text())
        self.assertEqual(state['state'],'BLOCKED')
        self.assertEqual(state['fetch_attempts'],30)
        self.assertFalse(state['training_enabled'])


if __name__=='__main__':unittest.main()
