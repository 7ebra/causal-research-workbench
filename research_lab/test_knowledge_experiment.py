import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import knowledge_experiment as e
from test_screener import fixture


class KnowledgeExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'status.json').write_text(json.dumps({'state':'ONLINE'}))

    def test_running_feed_blocks_fitting_before_model_training(self):
        with mock.patch.object(e,'verify',return_value=({},self.root)),mock.patch.object(e.s,'train') as train:
            with self.assertRaisesRegex(ValueError,'WAITING_FOR_COLLECTION_COMPLETION'):e.fit(self.root)
            train.assert_not_called()

    def test_completion_alone_cannot_bypass_assessment(self):
        (self.root/'status.json').write_text(json.dumps({'state':'COMPLETED'}))
        self.assertEqual(e.fitting_gate(self.root),'WAITING_FOR_COLLECTION_ASSESSMENT')
        (self.root/'research-assessment.json').write_text(json.dumps({'collection_state':'COMPLETED',
            'history_integrity':'PASS','historical_fitting_allowed':False}))
        self.assertEqual(e.fitting_gate(self.root),'DATA_ASSESSMENT_BLOCKS_FITTING')

    def test_modified_protocol_is_rejected(self):
        protocol=self.root/'protocol.json';protocol.write_text('{}')
        (self.root/'protocol-lock.json').write_text(json.dumps({'protocol_sha256':e.digest(protocol)}))
        protocol.write_text('{"payout":1}')
        with self.assertRaisesRegex(ValueError,'protocol changed'):e.verify(self.root)

    def test_rejected_validation_never_scores_reserved_test(self):
        (self.root/'research-assessment.json').write_text('{}')
        (self.root/'protocol.json').write_text('{}')
        rows=fixture();start=rows[0]['t'];train_end=rows[750]['t'];valid_end=rows[975]['t']
        protocol={'assets':['EUR_USD'],'horizons_minutes':[5],
                  'representations':['original_price','education_price'],'seeds':[101,202,303],
                  'split':{'start':start,'train_end':train_end,'validation_end':valid_end,'end_exclusive':rows[-1]['t']+300}}
        def evaluate(data,states,model,begin,end,horizon,*args,**kwargs):
            self.assertEqual((begin,end),(train_end,valid_end))
            return {'trades':0,'pnl':0,'market_days':10,'daily_mean_ci95':[0,0],
                    'total_loss_stop_reached':False,'daily':{},'ledger':[]}
        with mock.patch.object(e,'verify',return_value=(protocol,self.root)),mock.patch.object(e,'fitting_gate',return_value='READY_FOR_DIAGNOSTIC_FITTING'),mock.patch.object(e.audit_history,'audit'),mock.patch.object(e.s,'load_candles',return_value=rows),mock.patch.object(e.s,'train',return_value={'q':{},'support':{}}),mock.patch.object(e.s,'evaluate',side_effect=evaluate),mock.patch('builtins.print'):
            result=e.fit(self.root)
        self.assertEqual(result['state'],'REJECTED_AT_VALIDATION')
        self.assertFalse(result['historical_test_scored'])
        self.assertFalse(result['edge_established'])
        self.assertFalse((self.root/'selected-model.json').exists())

    def test_selection_is_frozen_before_any_historical_test_access(self):
        (self.root/'research-assessment.json').write_text('{}')
        (self.root/'protocol.json').write_text('{}')
        rows=fixture();start=rows[0]['t'];train_end=rows[750]['t'];valid_end=rows[975]['t']
        protocol={'assets':['EUR_USD'],'horizons_minutes':[5],
                  'representations':['original_price','education_price'],'seeds':[101,202,303],
                  'split':{'start':start,'train_end':train_end,'validation_end':valid_end,'end_exclusive':rows[-1]['t']+300}}
        def train(data,states,*args):
            return {'q':{'type':[len(states[-1].split(',')),0,0]},'support':{'type':[100,100,100]}}
        accesses=[]
        def evaluate(data,states,model,begin,end,horizon,*args,**kwargs):
            if begin==valid_end:
                self.assertTrue((self.root/'selection-lock.json').exists())
                lock=json.loads((self.root/'selection-lock.json').read_text())
                self.assertEqual(lock['model_sha256'],e.digest(self.root/'selected-model.json'))
                accesses.append(begin)
            return {'trades':100,'pnl':0 if kwargs.get('policy') else 10 if model['q']['type'][0]==3 else 5,
                    'market_days':20,'daily_mean_ci95':[1,2],
                    'total_loss_stop_reached':False,'daily':{},'ledger':[]}
        with mock.patch.object(e,'verify',return_value=(protocol,self.root)),mock.patch.object(e,'fitting_gate',return_value='READY_FOR_DIAGNOSTIC_FITTING'),mock.patch.object(e.audit_history,'audit'),mock.patch.object(e.s,'load_candles',return_value=rows),mock.patch.object(e.s,'train',side_effect=train),mock.patch.object(e.s,'evaluate',side_effect=evaluate),mock.patch.object(e,'spread_sensitivity',return_value={'hypothetical_pnl':0}),mock.patch('builtins.print'):
            result=e.fit(self.root)
        self.assertTrue(accesses)
        self.assertTrue(result['historical_test_scored'])
        self.assertFalse(result['edge_established'])


if __name__=='__main__':unittest.main()
