import io
import json
import unittest
from unittest import mock
import forward as f


class QuotePipeTests(unittest.TestCase):
    def test_local_snapshot_shape(self):
        snapshots=[{'asset':'EURUSD','error':'Test fixture only'}]
        with mock.patch.object(f.sys,'stdin',io.StringIO(json.dumps(snapshots)+'\n')),mock.patch('builtins.print'):
            self.assertEqual(f.collect_stdin(),snapshots)

    def test_missing_parent_exits_instead_of_retrying_a_dead_pipe(self):
        with mock.patch.object(f.sys,'stdin',io.StringIO('')),mock.patch('builtins.print'):
            with self.assertRaises(EOFError):f.collect_stdin()

    def test_invalid_snapshot_is_rejected(self):
        with mock.patch.object(f.sys,'stdin',io.StringIO('{}\n')),mock.patch('builtins.print'):
            with self.assertRaises(ValueError):f.collect_stdin()


if __name__=='__main__':unittest.main()
