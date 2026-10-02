"""Durable, observation-time forward paper experiment using reference quotes."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import os
import random
import sqlite3
import subprocess
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
import screener as s
import connectivity

POLICIES = ('rl', 'up', 'down', 'momentum', 'random', 'skip')
MAX_QUOTE_AGE = 90
MAX_SIGNAL_AGE = 360
MAX_SETTLEMENT_DELAY = 90
CLOSE_GRACE = 10


def finite(value):
    return isinstance(value, (float,int)) and not isinstance(value,bool) and math.isfinite(value)


def normalize(snapshot):
    if not isinstance(snapshot,dict):
        raise ValueError('Snapshot must be an object')
    if 'error' in snapshot:
        raise ValueError(snapshot['error'])
    asset, now, meta = snapshot['asset'], snapshot['received_at'], snapshot['meta']
    if not isinstance(meta,dict):
        raise ValueError('Quote metadata must be an object')
    if asset not in ('EURUSD','USDJPY','AUDUSD') or not finite(now):
        raise ValueError('Invalid asset or observation time')
    quote_time, price = meta.get('regularMarketTime'), meta.get('regularMarketPrice')
    if not finite(quote_time) or not finite(price) or price <= 0 or quote_time > now+5:
        raise ValueError('Invalid reference quote timestamp or price')
    times, values = snapshot['timestamps'], snapshot['quote']
    if not isinstance(values,dict):
        raise ValueError('Candle values must be an object')
    if not isinstance(times,list) or not times:
        raise ValueError('Missing candles')
    bars, invalid = [], 0
    previous = -1
    for i,t in enumerate(times):
        if not finite(t) or t != int(t) or t <= previous:
            raise ValueError('Candle timestamps must be strictly increasing integer seconds')
        previous = t
        # Yahoo can append a nonaligned quote row; it is not a closed candle.
        if t % s.BAR or t+s.BAR > now-CLOSE_GRACE:
            continue
        try:
            prices = {k: values[k][i] for k in ('open','high','low','close')}
        except (KeyError,IndexError,TypeError):
            invalid += 1
            continue
        if not all(finite(v) and v > 0 for v in prices.values()):
            invalid += 1
            continue
        if not prices['low'] <= min(prices['open'],prices['close']) <= max(prices['open'],prices['close']) <= prices['high']:
            invalid += 1
            continue
        bars.append(dict(prices,t=int(t)))
    if not bars:
        raise ValueError('No valid closed candles')
    return {'asset':asset,'received_at':now,'quote_time':int(quote_time),'price':float(price),
            'quote_age':now-quote_time,'bars':bars,'invalid_bars':invalid,
            'raw_sha256':hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()}


def verified_models(directory):
    manifest=json.loads((directory/'manifest.json').read_text())
    models={}
    for key,digest in manifest['hashes'].items():
        path=directory/f'{key}.json'
        contents=path.read_bytes()
        if hashlib.sha256(contents).hexdigest()!=digest:
            raise ValueError(f'Frozen model hash changed: {key}')
        models[key]=json.loads(contents)
    if len(models)!=9:
        raise ValueError('Exactly nine frozen candidates are required')
    return models,manifest


class PaperMonitor:
    def __init__(self,database,models,now,duration_seconds=86400):
        self.db=sqlite3.connect(database,timeout=15)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS bars (asset TEXT,t INTEGER,first_seen REAL,o REAL,h REAL,l REAL,c REAL,PRIMARY KEY(asset,t));
          CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY,observed_at REAL,asset TEXT,kind TEXT,details TEXT);
          CREATE TABLE IF NOT EXISTS quotes (asset TEXT,quote_t INTEGER,observed_at REAL,price REAL,age REAL,PRIMARY KEY(asset,quote_t,observed_at));
          CREATE TABLE IF NOT EXISTS accounts (key TEXT PRIMARY KEY,model_key TEXT,policy TEXT,state TEXT);
          CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY,account_key TEXT,entry_observed REAL,entry_quote_t INTEGER,
            expiry_target REAL,settled_observed REAL,exit_quote_t INTEGER,direction INTEGER,entry REAL,exit REAL,payout REAL,
            stake REAL,pnl REAL,status TEXT,settlement_delay REAL,decision_bar_t INTEGER);
        ''')
        fingerprint=hashlib.sha256(json.dumps(models,sort_keys=True).encode()).hexdigest()
        saved=self.meta('model_fingerprint')
        if saved and fingerprint!=saved:
            self.db.close()
            raise ValueError('Experiment models differ from the stored frozen models')
        self.models=models
        with self.db:
            if not saved:
                self.set_meta('model_fingerprint',fingerprint)
                self.set_meta('started_at',str(now))
                self.set_meta('ends_at',str(now+duration_seconds))
                self.set_meta('status','RUNNING')
                for key,model in models.items():
                    for policy in POLICIES:
                        state={'balance':1000.0,'peak':1000.0,'max_drawdown':0.0,'daily':{},
                               'pending':None,'last_bar':None,'next_entry_after':0,'wins':0,'losses':0,'ties':0,
                               'voids':0,'signals':0,'skips':0,'research_only':True}
                        self.db.execute('INSERT INTO accounts VALUES(?,?,?,?)',
                            (f'{key}/{policy}',key,policy,json.dumps(state)))

    def meta(self,key):
        row=self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        return row[0] if row else None

    def set_meta(self,key,value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,str(value)))

    def event(self,now,asset,kind,details):
        self.db.execute('INSERT INTO events(observed_at,asset,kind,details) VALUES(?,?,?,?)',
                        (now,asset,kind,json.dumps(details,sort_keys=True)))

    def settle(self,key,state,quote,now):
        pending=state['pending']
        if not pending or now < pending['expiry']:
            return
        lateness=now-pending['expiry']
        valid=(quote is not None and now-quote['quote_time'] <= MAX_QUOTE_AGE
               and quote['quote_time'] >= pending['expiry'] and lateness <= MAX_SETTLEMENT_DELAY)
        if not valid and lateness <= MAX_SETTLEMENT_DELAY:
            return
        if valid:
            price=quote['price']
            pnl=round(s.payoff(pending['price'],price,pending['action'],pending['payout'])*10,8)
            status='TIE' if price==pending['price'] else 'WIN' if pnl>0 else 'LOSS'
            state[{'WIN':'wins','LOSS':'losses','TIE':'ties'}[status]]+=1
            state['balance']=round(state['balance']+pnl,8)
            day=pending['entry_day']
            state['daily'][day]=round(state['daily'].get(day,0)+pnl,8)
            state['peak']=max(state['peak'],state['balance'])
            state['max_drawdown']=max(state['max_drawdown'],state['peak']-state['balance'])
        else:
            price,pnl,status=None,None,'VOID_MISSING_SETTLEMENT'
            state['voids']+=1
        self.db.execute('UPDATE trades SET settled_observed=?,exit_quote_t=?,exit=?,pnl=?,status=?,settlement_delay=? WHERE id=?',
            (now,quote['quote_time'] if valid else None,price,pnl,status,lateness,pending['id']))
        self.event(now,key,'SETTLED',{'trade_id':pending['id'],'status':status,'pnl':pnl,'delay':lateness})
        state['pending']=None
        state['next_entry_after']=now+s.BAR

    def tick(self,snapshots,now):
        normalized={}
        with self.db:
            for raw in snapshots:
                try:
                    q=normalize(raw)
                    # Reject an inconsistent collector clock; do not backdate observations.
                    if abs(q['received_at']-now)>60:
                        raise ValueError('Collector timestamp differs from processing clock by more than 60 seconds')
                    normalized[q['asset']]=q
                    revised=0
                    for bar in q['bars']:
                        existing=self.db.execute('SELECT o,h,l,c FROM bars WHERE asset=? AND t=?',(q['asset'],bar['t'])).fetchone()
                        prices=tuple(bar[k] for k in ('open','high','low','close'))
                        if existing is not None and existing!=prices:
                            revised+=1
                        self.db.execute('INSERT OR IGNORE INTO bars VALUES(?,?,?,?,?,?,?)',
                            (q['asset'],bar['t'],q['received_at'],*prices))
                    self.db.execute('INSERT OR IGNORE INTO quotes VALUES(?,?,?,?,?)',
                        (q['asset'],q['quote_time'],q['received_at'],q['price'],q['quote_age']))
                    self.event(now,q['asset'],'SNAPSHOT',{'quote_age':q['quote_age'],'closed_bars':len(q['bars']),
                        'invalid_bars':q['invalid_bars'],'revisions_ignored':revised,'raw_sha256':q['raw_sha256']})
                except (ValueError,KeyError,TypeError) as error:
                    self.event(now,raw.get('asset','unknown') if isinstance(raw,dict) else 'unknown',
                               'FEED_ERROR',{'error':str(error)})
            account_rows=self.db.execute('SELECT key,model_key,policy,state FROM accounts').fetchall()
            for key,model_key,policy,serialized in account_rows:
                model=self.models[model_key]
                asset=model['asset']
                state=json.loads(serialized)
                q=normalized.get(asset)
                self.settle(key,state,q,now)
                if q:
                    closed=self.db.execute('SELECT t,o,h,l,c FROM bars WHERE asset=? ORDER BY t DESC LIMIT 65',(asset,)).fetchall()[::-1]
                    rows=[dict(t=x[0],open=x[1],high=x[2],low=x[3],close=x[4]) for x in closed]
                    bar_t=rows[-1]['t']
                    # Process only the newest closed bar, once; never replay missed signals.
                    if state['last_bar'] != bar_t:
                        state['last_bar']=bar_t
                        state['signals']+=1
                        reason=None
                        feature=s.features(rows)[-1]
                        policy_detail=s.explain_choice(model,feature) if policy=='rl' else None
                        if now-q['quote_time']>MAX_QUOTE_AGE:
                            reason='STALE_QUOTE'
                        elif now-(bar_t+s.BAR)>MAX_SIGNAL_AGE:
                            reason='STALE_CANDLE'
                        elif feature is None:
                            reason='GAP_OR_WARMUP'
                        elif state['pending']:
                            reason='POSITION_OPEN'
                        elif now<state['next_entry_after']:
                            reason='COOLDOWN'
                        elif state['balance']<=900 or state['daily'].get(s.stamp(now)[:10],0)<=-30:
                            reason='LOSS_LIMIT'
                        elif now+model['expiry_minutes']*60>float(self.meta('ends_at')):
                            reason='RUN_END_TOO_CLOSE'
                        if reason:
                            action=0
                        elif policy=='rl':
                            action=policy_detail['action']
                        elif policy=='momentum':
                            action=1 if rows[-1]['close']>rows[-4]['close'] else 2
                        elif policy=='random':
                            seed=int(hashlib.sha256(f'{key}/{bar_t}'.encode()).hexdigest()[:16],16)
                            action=random.Random(seed).choice((1,2))
                        else:
                            action={'up':1,'down':2,'skip':0}[policy]
                        if action:
                            expiry=now+model['expiry_minutes']*60
                            cursor=self.db.execute('''INSERT INTO trades(account_key,entry_observed,entry_quote_t,expiry_target,
                              direction,entry,payout,stake,status,decision_bar_t) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                              (key,now,q['quote_time'],expiry,action,q['price'],model['payout_assumed'],10,'OPEN',bar_t))
                            state['pending']={'id':cursor.lastrowid,'expiry':expiry,'action':action,
                                'price':q['price'],'payout':model['payout_assumed'],'entry_day':s.stamp(now)[:10]}
                        else:
                            state['skips']+=1
                        self.event(now,key,'DECISION',{'state':feature,'bar_t':bar_t,'action':s.ACTIONS[action],
                            'reason':reason or ('MODEL_SKIP' if not action else 'RESEARCH_PAPER_ONLY'),
                            'policy_diagnostic':policy_detail,
                            'quote_time':q['quote_time'],'quote_age':now-q['quote_time']})
                self.db.execute('UPDATE accounts SET state=? WHERE key=?',(json.dumps(state),key))
            self.set_meta('last_poll',now)
            self.set_meta('polls',int(self.meta('polls') or 0)+1)
        return normalized

    def finish(self,now,status='COMPLETED'):
        with self.db:
            for key,serialized in self.db.execute('SELECT key,state FROM accounts').fetchall():
                state=json.loads(serialized)
                pending=state['pending']
                if pending:
                    self.db.execute('UPDATE trades SET settled_observed=?,status=? WHERE id=?',
                                    (now,'VOID_RUN_ENDED',pending['id']))
                    state['pending']=None
                    state['voids']+=1
                    self.db.execute('UPDATE accounts SET state=? WHERE key=?',(json.dumps(state),key))
            self.set_meta('status',status)
            self.set_meta('finished_at',now)

    def summary(self,now):
        accounts=[]
        rl_reasons={}
        for key,serialized in self.db.execute("SELECT asset,details FROM events WHERE kind='DECISION'"):
            if key.endswith('/rl'):
                decision=json.loads(serialized)
                reason=decision['reason']
                if reason=='MODEL_SKIP':
                    reason=(decision.get('policy_diagnostic') or {}).get('reason',reason)
                rl_reasons[reason]=rl_reasons.get(reason,0)+1
        for key,model_key,policy,serialized in self.db.execute('SELECT key,model_key,policy,state FROM accounts ORDER BY key'):
            state=json.loads(serialized)
            accounts.append(dict(key=key,model_key=model_key,policy=policy,**state,
                                 pnl=round(state['balance']-1000,8),trades=state['wins']+state['losses']+state['ties']))
        freshness={}
        for asset in ('EURUSD','USDJPY','AUDUSD'):
            row=self.db.execute('SELECT quote_t,observed_at,price FROM quotes WHERE asset=? ORDER BY quote_t DESC LIMIT 1',(asset,)).fetchone()
            freshness[asset]=dict(quote_at=s.stamp(row[0]),observed_at=s.stamp(row[1]),price=row[2],current_age_seconds=now-row[0]) if row else None
        started=float(self.meta('started_at'))
        rl_entries=sum(a['wins']+a['losses']+a['ties']+a['voids']+bool(a['pending'])
                       for a in accounts if a['policy']=='rl')
        activity_warning=('RL entry activity is low. Inspect policy skip reasons and feed blocks; '
                          'elapsed collection time is not strategy evidence.'
                          if sum(rl_reasons.values())>=100 and rl_entries<5 else None)
        stats=self.db.execute('SELECT avg(age),max(age),count(DISTINCT asset || ":" || quote_t) FROM quotes').fetchone()
        return {'status':self.meta('status'),'started_at':s.stamp(started),
            'ends_at':s.stamp(float(self.meta('ends_at'))),'updated_at':s.stamp(now),
            'polls':int(self.meta('polls') or 0),'accounts':accounts,'latest_quotes':freshness,
            'rl_decision_reasons':rl_reasons,
            'rl_activity_warning':activity_warning,
            'connection':json.loads(self.meta('connection') or '{}'),
            'last_poll_at':s.stamp(float(self.meta('last_poll') or self.meta('started_at'))),
            'quotes_recorded':self.db.execute('SELECT count(*) FROM quotes').fetchone()[0],
            'candles_recorded':self.db.execute('SELECT count(*) FROM bars').fetchone()[0],
            'forward_closed_bars':self.db.execute('SELECT count(*) FROM bars WHERE t>=?',(started,)).fetchone()[0],
            'quote_age_mean_seconds':stats[0], 'quote_age_max_seconds':stats[1], 'distinct_provider_quotes':stats[2],
            'feed_errors':self.db.execute("SELECT count(*) FROM events WHERE kind='FEED_ERROR'").fetchone()[0],
            'research_only':True,'promotion':'BLOCKED: rejected candidates, reference quotes, short forward record',
            'limitations':['External reference quotes; Quotex prices, payouts and execution are not validated.',
                'Paper fills use observed reference quotes with provider timestamp; expiry samples can be up to 90 seconds late.',
                'Voided settlements are recorded separately and excluded from profit; missing data can bias apparent performance.',
                '54 independent virtual accounts are comparisons, not a combined portfolio.',
                'Computer must remain running and connected. A 24-hour pilot cannot establish a reliable trading edge.']}


def atomic_json(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2),encoding='utf-8')
    return replace_export(temporary,path)


def replace_export(temporary,path):
    """Readers can briefly lock exported files on Windows; SQLite is authoritative."""
    for attempt in range(4):
        try:
            os.replace(temporary,path)
            return True
        except PermissionError as error:
            if getattr(error,'winerror',None) not in (5,32,33):raise
            if attempt<3:time.sleep(.1*(attempt+1))
    return False


def publish(monitor,out,now):
    summary=monitor.summary(now)
    delayed=[]
    summary['export_delays']=json.loads(monitor.meta('export_delays') or '[]')
    if not atomic_json(out/'status.json',summary):delayed.append('status.json')
    rows=monitor.db.execute('SELECT * FROM trades ORDER BY id').fetchall()
    columns=[x[1] for x in monitor.db.execute('PRAGMA table_info(trades)')]
    temp=out/'forward_trades.csv.tmp'
    with temp.open('w',newline='',encoding='utf-8') as f:
        writer=csv.writer(f);writer.writerow(columns);writer.writerows(rows)
    if not replace_export(temp,out/'forward_trades.csv'):delayed.append('forward_trades.csv')
    template=(s.ROOT/'forward_dashboard.html').read_text(encoding='utf-8')
    html=template.replace('__INITIAL_JSON__',json.dumps(summary).replace('<','\\u003c'))
    temp=out/'dashboard.html.tmp';temp.write_text(html,encoding='utf-8')
    if not replace_export(temp,out/'dashboard.html'):delayed.append('dashboard.html')
    previous=json.loads(monitor.meta('export_delays') or '[]')
    with monitor.db:
        monitor.set_meta('export_delays',json.dumps(delayed))
        if delayed!=previous:
            monitor.event(now,'all','EXPORT_DELAYED' if delayed else 'EXPORT_RECOVERED',{'files':delayed})
    summary['export_delays']=delayed
    if summary['status']!='RUNNING':
        rl=[a for a in summary['accounts'] if a['policy']=='rl']
        note=f"Status: {summary['status']}\nQuotes: {summary['quotes_recorded']}\nFeed errors: {summary['feed_errors']}\n"
        note+='\nFrozen RL candidates (simulated units):\n'
        note+='\n'.join(f"{a['key']}: {a['trades']} settled, P/L {a['pnl']}, voids {a['voids']}" for a in rl)
        note+='\n\nNo candidate is promoted by this pilot. Inspect voids, timestamps and feed errors before interpreting profit.\n'
        note+=f"Mean observed quote age: {summary['quote_age_mean_seconds']} seconds\n"
        note+=f"Max observed quote age: {summary['quote_age_max_seconds']} seconds\n"
        note+=f"New closed bars after start: {summary['forward_closed_bars']}\n"
        (out/'assessment.txt').write_text(note,encoding='utf-8')
    return summary


def collect(node):
    process=subprocess.run([node,str(s.ROOT/'collect_snapshot.mjs')],capture_output=True,text=True,timeout=55)
    if process.returncode:
        raise RuntimeError(process.stderr[-500:])
    snapshots=json.loads(process.stdout)
    if not isinstance(snapshots,list) or any(not isinstance(x,dict) for x in snapshots):
        raise ValueError('Collector returned an invalid response shape')
    return snapshots


def collect_stdin():
    try:print(json.dumps({'type':'SNAPSHOT_REQUEST'}),flush=True)
    except BrokenPipeError:raise EOFError('Local quote collector disconnected')
    line=sys.stdin.readline()
    if not line:raise EOFError('Local quote collector disconnected')
    snapshots=json.loads(line)
    if not isinstance(snapshots,list) or any(not isinstance(x,dict) for x in snapshots):
        raise ValueError('Local collector returned invalid snapshots')
    return snapshots


def connection_step(monitor,accepted,snapshots,now,poll_seconds,error=None):
    errors={x.get('asset','unknown'):str(x.get('error','Invalid snapshot; see feed errors'))[:500]
            for x in snapshots if x.get('asset') not in accepted}
    if error:
        errors['collector']=str(error)[:500]
    previous=json.loads(monitor.meta('connection') or '{}')
    status,event=connectivity.advance(previous,accepted,now,poll_seconds,errors)
    with monitor.db:
        monitor.set_meta('connection',json.dumps(status))
        if event:
            monitor.event(now,'all',event,status)
    return status


def wait_for_retry(monitor,out,retry_at,clock=time.time,sleep=time.sleep,publisher=publish):
    """Keep reports alive and honour stop/reload/deadline during a network backoff."""
    deadline=min(retry_at,float(monitor.meta('ends_at')))
    while clock()<deadline:
        if (out/'STOP').exists() or (out/'RELOAD').exists():
            return
        sleep(min(15,max(0,deadline-clock())))
        publisher(monitor,out,clock())


def run(args):
    out=args.out
    out.mkdir(parents=True,exist_ok=True)
    # OS file locking is released even after a crash; prevent two writers.
    handle=(out/'worker.lock').open('a+b');handle.seek(0)
    if handle.read(1)==b'':handle.write(b'0');handle.flush()
    handle.seek(0)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    models,manifest=verified_models(args.models)
    atomic_json(out/'model_manifest.json',manifest)
    monitor=PaperMonitor(out/'experiment.sqlite',models,time.time(),args.hours*3600)
    if monitor.meta('status')!='RUNNING':
        raise ValueError('This experiment already finished. Use a new output directory.')
    if not atomic_json(out/'worker.json',{'pid':os.getpid(),'started_at':s.stamp(time.time()),'poll_seconds':args.poll_seconds,'models':str(args.models.resolve())}):
        raise RuntimeError('Worker identity file is locked; collector was not started')
    with monitor.db:
        monitor.event(time.time(),'all','WORKER_STARTED',{'pid':os.getpid(),'connection_handler_version':1})
    try:
        while True:
            now=time.time()
            if now<float(monitor.meta('last_poll') or monitor.meta('started_at'))-5:
                raise RuntimeError('System clock moved backwards; experiment stopped')
            if (out/'STOP').exists():
                monitor.finish(now,'STOPPED');publish(monitor,out,now);break
            if (out/'RELOAD').exists():
                with monitor.db:monitor.event(now,'all','WORKER_RELOAD',{'pid':os.getpid()})
                (out/'RELOAD').unlink()
                publish(monitor,out,now)
                break
            if now>=float(monitor.meta('ends_at')):
                monitor.finish(now);publish(monitor,out,now);break
            try:
                snapshots=collect_stdin() if getattr(args,'stdin_feed',False) else collect(args.node)
                now=time.time()
                accepted=monitor.tick(snapshots,now)
                connection=connection_step(monitor,accepted,snapshots,now,args.poll_seconds)
            except (RuntimeError,subprocess.TimeoutExpired,ValueError,OSError) as error:
                now=time.time()
                with monitor.db:monitor.event(now,'all','FEED_ERROR',{'error':str(error)[:500]})
                monitor.tick([],now)
                connection=connection_step(monitor,{},[],now,args.poll_seconds,error)
            summary=publish(monitor,out,now)
            log={k:summary[k] for k in ('updated_at','status','polls','quotes_recorded','feed_errors')}
            log['connection']=connection['state']
            log['retry_seconds']=connection['retry_seconds']
            log['export_delays']=summary['export_delays']
            print(json.dumps(log),flush=True)
            if args.once:
                monitor.finish(now,'SNAPSHOT_CHECK')
                publish(monitor,out,now)
                break
            wait_for_retry(monitor,out,connection['next_attempt_at'])
    except BaseException:
        with monitor.db:monitor.event(time.time(),'all','WORKER_INTERRUPTED',{'last_poll':monitor.meta('last_poll')})
        publish(monitor,out,time.time())
        raise
    finally:
        monitor.db.close();handle.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=s.ROOT/'forward-pilot')
    p.add_argument('--models',type=Path,default=s.ROOT/'frozen_candidates')
    p.add_argument('--hours',type=float,default=24)
    p.add_argument('--poll-seconds',type=int,default=60)
    p.add_argument('--node',default='node')
    p.add_argument('--once',action='store_true')
    p.add_argument('--stdin-feed',action='store_true',help='Receive public quote snapshots over a local pipe')
    args=p.parse_args()
    if not 0<args.hours<=720 or not 30<=args.poll_seconds<=60:p.error('hours must be (0,720], polling 30..60 seconds')
    run(args)
