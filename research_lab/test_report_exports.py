import tempfile
import unittest
from pathlib import Path
from unittest import mock
import forward as f
from test_forward import models, NOW, raw


def lock_error():
    error=PermissionError('File temporarily locked');error.winerror=5;return error


class ReportExportTests(unittest.TestCase):
    def test_temporary_lock_retries_then_succeeds(self):
        with mock.patch.object(f.os,'replace',side_effect=[lock_error(),None]) as replace,mock.patch.object(f.time,'sleep'):
            self.assertTrue(f.replace_export(Path('temp'),Path('report')))
            self.assertEqual(replace.call_count,2)

    def test_persistent_reader_lock_is_bounded(self):
        with mock.patch.object(f.os,'replace',side_effect=lock_error()) as replace,mock.patch.object(f.time,'sleep'):
            self.assertFalse(f.replace_export(Path('temp'),Path('report')))
            self.assertEqual(replace.call_count,4)

    def test_other_disk_failures_are_not_hidden(self):
        with mock.patch.object(f.os,'replace',side_effect=OSError('Disk error')):
            with self.assertRaises(OSError):f.replace_export(Path('temp'),Path('report'))

    def test_locked_dashboard_does_not_stop_paper_state_or_following_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);monitor=f.PaperMonitor(out/'test.sqlite',models(),NOW)
            monitor.tick([raw()],NOW)
            real_replace=f.os.replace
            def simulated_lock(source,target):
                if Path(target).name=='dashboard.html':raise lock_error()
                return real_replace(source,target)
            try:
                with mock.patch.object(f.os,'replace',side_effect=simulated_lock),mock.patch.object(f.time,'sleep'):
                    result=f.publish(monitor,out,NOW)
                self.assertEqual(result['export_delays'],['dashboard.html'])
                self.assertEqual(monitor.meta('status'),'RUNNING')
                monitor.tick([raw(now=NOW+301,price=1.13)],NOW+301)
                result=f.publish(monitor,out,NOW+301)
                self.assertEqual(result['export_delays'],[])
                self.assertTrue((out/'dashboard.html').exists())
                self.assertEqual(monitor.db.execute("SELECT count(*) FROM events WHERE kind='EXPORT_RECOVERED'").fetchone()[0],1)
                self.assertGreater(sum(x['trades'] for x in result['accounts']),0)
            finally:monitor.db.close()


if __name__=='__main__':unittest.main()
