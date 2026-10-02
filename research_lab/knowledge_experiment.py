"""Staged, immutable knowledge-assisted RL diagnostic; no orders or online updates."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import audit_history
import knowledge_features as knowledge
import screener as s

ROOT=Path(__file__).resolve().parent
POLICIES=('skip','up','down','momentum','random')
CODE=('knowledge_experiment.py','knowledge_features.py','screener.py','audit_history.py')


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,value):
    with path.open('x',encoding='utf-8') as file:json.dump(value,file,indent=2)


def fitting_gate(study):
    status=json.loads((study/'status.json').read_text())
    if status['state']!='COMPLETED':return 'WAITING_FOR_COLLECTION_COMPLETION'
    file=study/'research-assessment.json'
    if not file.exists():return 'WAITING_FOR_COLLECTION_ASSESSMENT'
    assessment=json.loads(file.read_text())
    if assessment.get('collection_state')!='COMPLETED' or assessment.get('history_integrity')!='PASS' or assessment.get('historical_fitting_allowed') is not True:
        return 'DATA_ASSESSMENT_BLOCKS_FITTING'
    return 'READY_FOR_DIAGNOSTIC_FITTING'


def prepare(study,out):
    study=study.resolve(); out=out.resolve()
    if out.exists():raise ValueError('Trial exists; do not overwrite or retune an existing experiment')
    quality=audit_history.audit(study)
    history=study/'history'
    manifest=json.loads((history/'manifest.json').read_text())
    start=max(x['first_timestamp'] for x in quality['instruments'].values())
    end=min(x['last_timestamp'] for x in quality['instruments'].values())+s.BAR
    train_end=int((start+(end-start)*.5)//s.BAR)*s.BAR
    valid_end=int((start+(end-start)*.65)//s.BAR)*s.BAR
    protocol={'version':1,'created_at':s.stamp(datetime.now(timezone.utc).timestamp()),
        'study':str(study),'status':'DIAGNOSTIC_ONLY','orders_enabled':False,
        'assets':list(audit_history.ASSETS),'horizons_minutes':[5,10,15],
        'representations':['original_price','education_price'],
        'epochs':12,'seeds':[101,202,303],'payout':.8,
        'minimum_validation_market_days':10,'minimum_test_market_days':20,
        'minimum_trades':100,'stake':10,'starting_balance':1000,
        'daily_loss_threshold':30,'total_loss_threshold':100,
        'min_support':30,'min_q_advantage':.02,
        'split':{'start':start,'train_end':train_end,'validation_end':valid_end,'end_exclusive':end},
        'code_hashes':{name:digest(ROOT/name) for name in CODE},
        'source_registry_sha256':digest(ROOT/'knowledge_sources.json'),
        'history_manifest_sha256':digest(history/'manifest.json'),
        'data_hashes':manifest['data_hashes'],
        'selection':'At most one eligible education-price challenger; validation only. Must beat its original-price counterpart and all validation baselines.',
        'limitations':['Educational features, not a text-trained or autonomous learner.',
            'Dates overlap prior research; historical test is diagnostic, not an untouched prospective confirmation.',
            'Assumed binary rewards are not executable FX returns or Quotex settlements.',
            'Candidate selection and descriptive bootstrap intervals do not prove an edge.']}
    out.mkdir(parents=True)
    save(out/'history-integrity.json',quality)
    save(out/'protocol.json',protocol)
    save(out/'protocol-lock.json',{'protocol_sha256':digest(out/'protocol.json')})
    readiness={'state':fitting_gate(study),'trial':str(out),'models_fitted':0,'orders_enabled':False}
    save(out/'prepared.json',readiness)
    return readiness


def verify(out):
    lock=json.loads((out/'protocol-lock.json').read_text())
    if digest(out/'protocol.json')!=lock['protocol_sha256']:raise ValueError('Frozen protocol changed')
    protocol=json.loads((out/'protocol.json').read_text()); study=Path(protocol['study'])
    for name,expected in protocol['code_hashes'].items():
        if digest(ROOT/name)!=expected:raise ValueError('Frozen experiment code changed: '+name)
    if digest(ROOT/'knowledge_sources.json')!=protocol['source_registry_sha256']:
        raise ValueError('Source registry changed; use a separate trial')
    if digest(study/'history'/'manifest.json')!=protocol['history_manifest_sha256']:
        raise ValueError('Historical manifest changed')
    for asset,expected in protocol['data_hashes'].items():
        if digest(study/'history'/(asset.replace('_','')+'.csv'))!=expected:
            raise ValueError('Historical input changed: '+asset)
    return protocol,study


def eligibility(result,baselines,seeds,comparison=None):
    reasons=s.qualify(result,10)
    if result['pnl']<=max(v['pnl'] for v in baselines.values()):reasons.append('Did not beat every validation baseline')
    if any(v['pnl']<=0 for v in seeds):reasons.append('Not profitable across all three seeds')
    if comparison is not None and result['pnl']<=comparison['pnl']:
        reasons.append('No improvement over original-price counterpart')
    return reasons


def spread_sensitivity(study,asset,ledger):
    """Pessimistic bid/ask basis on the fixed mid ledger, not executable FX profit."""
    db=sqlite3.connect((study/'history'/'candles.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    try:
        opens={(t,side):price for t,side,price in db.execute(
            'SELECT t,side,o FROM candles WHERE instrument=?',(asset,))}
    finally:db.close()
    pnl=0.; changed=0
    for trade in ledger:
        entry=int(datetime.fromisoformat(trade['entry_time']).timestamp())
        expiry=int(datetime.fromisoformat(trade['expiry_time']).timestamp())
        action=s.ACTIONS.index(trade['direction'])
        ep=opens[(entry,'ask' if action==1 else 'bid')]
        xp=opens[(expiry,'bid' if action==1 else 'ask')]
        result=s.payoff(ep,xp,action,trade['payout_assumed'])*trade['stake']
        pnl+=result; changed+=abs(result-trade['pnl'])>1e-8
    return {'same_mid_ledger_entries':len(ledger),'hypothetical_pnl':round(pnl,8),
            'outcomes_changed':changed,'risk_stops_resimulated':False,
            'meaning':'Pessimistic quote-basis sensitivity only; neither executable FX profit nor Quotex settlement.'}


def fit(out):
    out=out.resolve(); protocol,study=verify(out)
    gate=fitting_gate(study)
    if gate!='READY_FOR_DIAGNOSTIC_FITTING':raise ValueError(gate)
    if (out/'fitting-started.json').exists():raise ValueError('Trial already attempted; preserve it and do not rerun selection')
    audit_history.audit(study)
    # An interrupted trial is preserved, not resumed with a fresh selection attempt.
    save(out/'fitting-started.json',{'started_at':s.stamp(datetime.now(timezone.utc).timestamp()),
        'protocol_sha256':digest(out/'protocol.json'),'assessment_sha256':digest(study/'research-assessment.json')})
    split=protocol['split']; start=split['start']; train_end=split['train_end']; valid_end=split['validation_end']; end=split['end_exclusive']
    candidates=[]; models={}; datasets={}; features={}
    for asset in protocol['assets']:
        rows=s.load_candles(study/'history'/(asset.replace('_','')+'.csv')); datasets[asset]=rows
        states={'original_price':s.features(rows),'education_price':knowledge.features(rows)}
        if [x is None for x in states['original_price']]!=[x is None for x in states['education_price']]:
            raise ValueError('Feature variants have different eligible observation coverage')
        features[asset]=states
        for minutes in protocol['horizons_minutes']:
            original=None
            for representation in protocol['representations']:
                key=f'{asset}-{minutes}/{representation}'
                print('Training diagnostic candidate '+key,flush=True)
                state=states[representation]
                trained=[s.train(rows,state,start,train_end,minutes//5,.8,seed,12) for seed in protocol['seeds']]
                model=s.combine(trained); models[key]=model
                result=s.evaluate(rows,state,model,train_end,valid_end,minutes//5)
                seeds=[s.compact(s.evaluate(rows,state,m,train_end,valid_end,minutes//5)) for m in trained]
                baselines={p:s.compact(s.evaluate(rows,state,model,train_end,valid_end,minutes//5,policy=p)) for p in POLICIES}
                reasons=eligibility(result,baselines,seeds,original if representation=='education_price' else None)
                row={'key':key,'asset':asset,'expiry_minutes':minutes,'representation':representation,
                     'validation':s.compact(result),'seed_validation':seeds,'validation_baselines':baselines,
                     'rejection_reasons':reasons,'eligible':not reasons}
                candidates.append(row)
                if representation=='original_price':original=result
    save(out/'validation.json',{'candidates':candidates,'candidate_count':len(candidates),
         'test_not_scored_yet':True,'orders_enabled':False})
    pool=[c for c in candidates if c['representation']=='education_price' and c['eligible']]
    pool.sort(key=lambda c:(c['validation']['daily_mean_ci95'][0],c['validation']['pnl'],c['key']),reverse=True)
    if not pool:
        report={'state':'REJECTED_AT_VALIDATION','selected':None,'edge_established':False,
                'historical_test_scored':False,'orders_enabled':False,'candidate_count':len(candidates),
                'next_step':'Stop this feature hypothesis; do not retune against the reserved test.'}
    else:
        chosen=pool[0]; key=chosen['key']; asset=chosen['asset']; horizon=chosen['expiry_minutes']//5
        save(out/'selected-model.json',{'candidate':chosen,'model':models[key],'protocol_sha256':digest(out/'protocol.json')})
        save(out/'selection-lock.json',{'model_sha256':digest(out/'selected-model.json'),
            'frozen_before_historical_test':True})
        rows=datasets[asset]; state=features[asset]['education_price']; model=models[key]
        test=s.evaluate(rows,state,model,valid_end,end,horizon)
        baseline={p:s.compact(s.evaluate(rows,state,model,valid_end,end,horizon,policy=p)) for p in POLICIES}
        control=s.compact(s.evaluate(rows,features[asset]['original_price'],models[f'{asset}-{horizon*5}/original_price'],valid_end,end,horizon))
        stress={f'payout_{p:.2f}':s.compact(s.evaluate(rows,state,model,valid_end,end,horizon,payout=p)) for p in (.7,.8,.9)}
        stress['one_bar_delay']=s.compact(s.evaluate(rows,state,model,valid_end,end,horizon,delay=1))
        stress['one_percent_stake_cost']=s.compact(s.evaluate(rows,state,model,valid_end,end,horizon,cost=.01))
        spread=spread_sensitivity(study,asset,test['ledger'])
        reasons=s.qualify(test,20)
        if test['pnl']<=max(v['pnl'] for v in baseline.values()):reasons.append('Did not beat every historical-test baseline')
        if test['pnl']<=control['pnl']:reasons.append('No historical-test improvement over original-price counterpart')
        if any(v['pnl']<=0 for v in stress.values()):reasons.append('A payout/timing/cost stress failed')
        if spread['hypothetical_pnl']<=0:reasons.append('Bid/ask quote-basis sensitivity did not remain positive')
        reasons.append('Reference data and retrospective selection cannot establish a prospective or broker-specific edge')
        report={'state':'HISTORICAL_DIAGNOSTIC_COMPLETE','selected':chosen,'edge_established':False,
            'historical_test_scored':True,'orders_enabled':False,'historical_test':s.compact(test),
            'original_price_control':control,'baselines':baseline,'stresses':stress,
            'bid_ask_sensitivity':spread,
            'promotion_blockers':reasons,'next_step':'No deployment. Define a new future paper protocol only if diagnostic screening supports it.'}
        with (out/'historical-trades.csv').open('x',newline='',encoding='utf-8') as file:
            fields=['decision_time','entry_time','expiry_time','direction','entry','exit','state','stake','payout_assumed','pnl','balance','result']
            writer=csv.DictWriter(file,fieldnames=fields);writer.writeheader();writer.writerows(test['ledger'])
    save(out/'report.json',report)
    lines=['# Knowledge-assisted RL diagnostic', '', 'No edge is established. No orders are enabled.', '',
           'State: '+report['state'], '', '| Candidate | Validation trades | P/L | Eligible |', '| --- | ---: | ---: | --- |']
    lines += [f"| {c['key']} | {c['validation']['trades']} | {c['validation']['pnl']} | {c['eligible']} |" for c in candidates]
    lines += ['', report['next_step'], '', 'Source-derived price features only; no automatic text learning or private provider feed.']
    with (out/'assessment.md').open('x',encoding='utf-8') as file:file.write('\n'.join(lines)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','fit'))
    parser.add_argument('--study',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.action=='prepare' and args.study is None:parser.error('--study is required to prepare')
    try:
        result=prepare(args.study,args.out) if args.action=='prepare' else fit(args.out)
        print(json.dumps(result,indent=2))
    except ValueError as error:parser.exit(2,str(error)+'\n')
