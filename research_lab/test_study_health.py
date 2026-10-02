import unittest
import study_health as s


class StudyHealthTests(unittest.TestCase):
    def test_fresh_data_and_process_are_healthy(self):
        self.assertEqual(s.health({'state':'ONLINE','last_message_ns':1000*10**9},True,1005)[0],'DATA_FLOWING')

    def test_dead_process_cannot_be_hidden_by_old_online_status(self):
        self.assertEqual(s.health({'state':'ONLINE','last_message_ns':1000*10**9},False,1005)[0],'PROCESS_NOT_RUNNING')

    def test_offline_retries_are_not_reported_as_healthy(self):
        self.assertEqual(s.health({'state':'RECONNECTING','last_message_ns':1000*10**9},True,1100)[0],'RECONNECTING')

    def test_stale_data_and_completion_are_distinguished(self):
        self.assertEqual(s.health({'state':'ONLINE','last_message_ns':1000*10**9},True,1100)[0],'STARTING_OR_DATA_STALE')
        self.assertEqual(s.health({'state':'COMPLETED'},False,1100)[0],'COMPLETED')


if __name__=='__main__':unittest.main()
