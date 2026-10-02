import sqlite3
import tempfile
from pathlib import Path
import unittest
import knowledge_registry as k


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.db=k.connect(Path(self.temp.name)/'notes.sqlite'); self.addCleanup(self.db.close)
        self.note={'url':'https://www.oanda.com/us-en/skills-and-insights/',
                   'category':'education','summary':'Example hypothesis; not evidence of predictive ability.'}

    def test_old_publication_cannot_backdate_first_observation(self):
        self.note['published_at']='2020-01-01T00:00:00Z'
        k.record(self.db,self.note,observed_at=1800000000)
        self.assertEqual(k.available_notes(self.db,1799999999),[])
        self.assertEqual(len(k.available_notes(self.db,1800000000)),1)

    def test_revision_cannot_change_previous_asof_view(self):
        k.record(self.db,self.note,observed_at=100)
        before=k.available_notes(self.db,100)
        self.note['summary']='A revised hypothesis.'
        k.record(self.db,self.note,observed_at=200)
        self.assertEqual(k.available_notes(self.db,100),before)
        self.assertEqual(k.available_notes(self.db,200)[0]['summary'],'A revised hypothesis.')
        self.assertEqual(self.db.execute('SELECT count(*) FROM notes').fetchone()[0],2)

    def test_identical_notes_are_deduplicated(self):
        self.assertTrue(k.record(self.db,self.note,100))
        self.assertFalse(k.record(self.db,self.note,200))

    def test_unreviewed_host_and_full_article_are_rejected(self):
        self.note['url']='https://www.oanda.com.example.test/article'
        with self.assertRaises(ValueError):k.record(self.db,self.note)
        self.note['url']='https://www.oanda.com/article';self.note['summary']='word '*101
        with self.assertRaises(ValueError):k.record(self.db,self.note)


if __name__=='__main__':unittest.main()
