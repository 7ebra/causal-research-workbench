"""Bounded official FX research: practice quotes, closed historical candles, clock diagnostics."""
import argparse
import csv
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode,quote
if __package__:
    from . import oanda_practice as p
else:
    import oanda_practice as p

ROOT=Path(__file__).resolve().parent.parent
UNIVERSE=('EUR_USD','USD_JPY','AUD_USD','CAD_JPY')


def atomic_json(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2),encoding='utf-8')
    for attempt in range(4):
        try:os.replace(temporary,path);return True
        except PermissionError as error:
            if getattr(error,'winerror',None) not in (5,32,33):raise
            if attempt<3:time.sleep(.1*(attempt+1))
    return False


def normalize_candle(candle,start,end):
    if candle.get('complete') is not True:return None
    timestamp=p.epoch_ns(candle['time'])//1_000_000_000
    if timestamp%300:raise ValueError('Historical candle is not M5-aligned')
    if not start<=timestamp<end or timestamp+300>end:return None
    sides={}
    for side in ('mid','bid','ask'):
        values={k:float(candle[side][k]) for k in ('o','h','l','c')}
        if not all(p.math.isfinite(v) and v>0 for v in values.values()):raise ValueError('Invalid historical candle price')
        if not values['l']<=min(values['o'],values['c'])<=max(values['o'],values['c'])<=values['h']:
            raise ValueError('Inconsistent historical OHLC')
        sides[side]=values
    return {'timestamp':timestamp,'sides':sides,'price_updates':int(candle.get('volume',0))}


def windows(start,end,days=4):
    cursor=start
    while cursor<end:
        stop=min(end,cursor+days*86400)
        yield cursor,stop
        cursor=stop


def utc(value):
    return datetime.fromtimestamp(value,timezone.utc).isoformat().replace('+00:00','Z')


def clock_probe(out):
    result={'observed_at':utc(time.time()),'server':'time.windows.com','system_clock_changed':False}
    try:
        proc=subprocess.run(['w32tm','/stripchart','/computer:time.windows.com','/dataonly','/samples:3'],
                            capture_output=True,text=True,timeout=15)
        offsets=[float(whole+'.'+fraction) for whole,fraction in re.findall(r',\s*([+-]\d+)\.(\d+)s',proc.stdout)]
        result.update(status='MEASURED' if offsets else 'UNAVAILABLE',offset_samples_seconds=offsets,
                      reference_ahead_of_local_median_seconds=statistics.median(offsets) if offsets else None)
    except (OSError,subprocess.TimeoutExpired):result['status']='UNAVAILABLE'
    result['limitation']='Read-only external-reference estimate; broker-clock alignment and network latency remain separate. Raw quote times are never rewritten.'
    with (out/'clock-probes.jsonl').open('a',encoding='utf-8') as file:file.write(json.dumps(result)+'\n')
    return result


def history_worker(config,out,start,end,stop_event,fetch=p.open_readonly):
    folder=out/'history';folder.mkdir(exist_ok=True)
    db=sqlite3.connect(folder/'candles.sqlite')
    db.execute('''CREATE TABLE IF NOT EXISTS candles(instrument TEXT,t INTEGER,side TEXT,o REAL,h REAL,l REAL,c REAL,
                  price_updates INTEGER,PRIMARY KEY(instrument,t,side))''')
    manifest={'provider':'OANDA practice','granularity':'M5','price_components':['mid','bid','ask'],
              'from':utc(start),'to_exclusive':utc(end),'state':'RUNNING','instruments':{},
              'note':'volume counts price updates, not consolidated FX traded volume'}
    try:
        for instrument in UNIVERSE:
            pages=0
            for beginning,finish in windows(start,end):
                if stop_event.is_set():manifest['state']='INTERRUPTED';break
                query=urlencode({'price':'MBA','granularity':'M5','from':utc(beginning),'to':utc(finish),
                                 'smooth':'false','includeFirst':'true'})
                url=p.API+'/v3/instruments/'+instrument+'/candles?'+query
                payload=None
                for attempt in range(3):
                    try:
                        with fetch(url,config['token'],timeout=20) as response:payload=json.load(response)
                        break
                    except (HTTPError,URLError,OSError,TimeoutError) as error:
                        if isinstance(error,HTTPError) and error.code in (400,401,403,404):raise
                        if attempt==2:raise
                        if stop_event.wait(2**attempt):raise RuntimeError('History interrupted')
                if payload.get('instrument')!=instrument or payload.get('granularity')!='M5':
                    raise ValueError('Historical response identifies a different instrument or granularity')
                with db:
                    for candle in payload['candles']:
                        normalized=normalize_candle(candle,beginning,finish)
                        if normalized:
                            for side,values in normalized['sides'].items():
                                db.execute('INSERT OR IGNORE INTO candles VALUES(?,?,?,?,?,?,?,?)',
                                           (instrument,normalized['timestamp'],side,*values.values(),normalized['price_updates']))
                pages+=1
                manifest['instruments'][instrument]={'pages':pages,'mid_candles':db.execute('SELECT count(*) FROM candles WHERE instrument=? AND side=?',(instrument,'mid')).fetchone()[0]}
                atomic_json(folder/'manifest.json',manifest)
                if stop_event.wait(.3):manifest['state']='INTERRUPTED';break
            if manifest['state']=='INTERRUPTED':break
        if manifest['state']=='RUNNING':manifest['state']='COMPLETED'
    except (HTTPError,URLError,OSError,TimeoutError,ValueError,KeyError,TypeError,RuntimeError) as error:
        manifest['state']='FAILED_OR_PARTIAL';manifest['error']=p.error_label(error)
    finally:
        for instrument in UNIVERSE:
            target=folder/(instrument.replace('_','')+'.csv')
            with target.open('w',newline='',encoding='utf-8') as file:
                writer=csv.writer(file);writer.writerow(['timestamp','open','high','low','close'])
                writer.writerows(db.execute('SELECT t,o,h,l,c FROM candles WHERE instrument=? AND side=? ORDER BY t',(instrument,'mid')))
            manifest.setdefault('data_hashes',{})[instrument]=hashlib.sha256(target.read_bytes()).hexdigest()
        atomic_json(folder/'manifest.json',manifest)
        db.close()


def record(out,hours,days,config):
    out.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(out/'prices.sqlite');db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''CREATE TABLE IF NOT EXISTS prices(instrument TEXT,source_time TEXT,source_ns INTEGER,received_ns INTEGER,
      receipt_monotonic_ns INTEGER,bid REAL,ask REAL,mid REAL,spread REAL,age_seconds REAL,tradeable INTEGER,
      PRIMARY KEY(instrument,source_ns));
      CREATE TABLE IF NOT EXISTS messages(received_ns INTEGER,receipt_monotonic_ns INTEGER,kind TEXT,source_ns INTEGER);
      CREATE TABLE IF NOT EXISTS events(observed_ns INTEGER,kind TEXT,details TEXT);
      CREATE TABLE IF NOT EXISTS run(started REAL,ends REAL,started_monotonic REAL);''')
    if db.execute('SELECT count(*) FROM run').fetchone()[0]:
        db.close();raise ValueError('Study already exists; choose a new output folder')
    start=time.time();mono_start=time.monotonic();end=start+hours*3600;mono_end=mono_start+hours*3600
    with db:db.execute('INSERT INTO run VALUES(?,?,?)',(start,end,mono_start))
    stop_event=threading.Event()
    cutoff=int(start//300)*300
    history=threading.Thread(target=history_worker,args=(config,out,cutoff-days*86400,cutoff,stop_event),daemon=True)
    history.start()
    def calibrate():
        while not stop_event.is_set():
            clock_probe(out)
            if stop_event.wait(3600):break
    calibration=threading.Thread(target=calibrate,daemon=True);calibration.start()
    state='CONNECTING';errors=recoveries=streak=0;last_message=None;next_publish=0
    def publish():
        values={}
        for instrument in UNIVERSE:
            row=db.execute('SELECT count(*),max(received_ns) FROM prices WHERE instrument=?',(instrument,)).fetchone()
            values[instrument]={'samples':row[0],'last_receipt_ns':row[1]}
        status={'mode':'OFFICIAL_FX_RESEARCH','state':state,'started_at':utc(start),'ends_at':utc(end),
                'updated_at':utc(time.time()),'elapsed_monotonic_seconds':time.monotonic()-mono_start,
                'errors':errors,'recoveries':recoveries,'last_message_ns':last_message,'instruments':values,
                'orders_enabled':False,'model_training_enabled':False,'credentials_persisted':False,
                'history_manifest':'history/manifest.json','limitation':'Quote capture and historical preparation only; no strategy or Quotex transfer approval.'}
        atomic_json(out/'status.json',status)
        return status
    url=p.STREAM+'/v3/accounts/'+quote(config['account'],safe='')+'/pricing/stream?instruments='+','.join(UNIVERSE)+'&snapshot=true'
    try:
        while time.monotonic()<mono_end and not (out/'STOP').exists():
            try:
                with p.open_readonly(url,config['token'],timeout=15) as response:
                    if streak:recoveries+=1
                    streak=0;state='ONLINE'
                    with db:db.execute('INSERT INTO events VALUES(?,?,?)',(time.time_ns(),'CONNECTED','Official practice stream'))
                    publish()
                    while time.monotonic()<mono_end and not (out/'STOP').exists():
                        line=response.readline()
                        if not line:raise URLError('Stream ended')
                        received=time.time_ns();monotonic=time.monotonic_ns();message=json.loads(line)
                        if not isinstance(message,dict) or message.get('type') not in ('PRICE','HEARTBEAT'):raise ValueError('Invalid stream message')
                        source=p.epoch_ns(message['time']);last_message=received
                        price=p.normalize(message,received,allowed=UNIVERSE)
                        with db:
                            db.execute('INSERT INTO messages VALUES(?,?,?,?)',(received,monotonic,message['type'],source))
                            if price:
                                db.execute('INSERT OR IGNORE INTO prices VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                           (price['instrument'],price['source_time'],price['source_ns'],received,monotonic,
                                            price['bid'],price['ask'],price['mid'],price['spread'],price['age_seconds'],price['tradeable']))
                        if time.monotonic()>=next_publish:publish();next_publish=time.monotonic()+15
            except (HTTPError,URLError,OSError,TimeoutError,ValueError,KeyError,IndexError,TypeError) as error:
                errors+=1;streak+=1;label=p.error_label(error)
                state='ACCESS_REQUIRED' if label in ('AUTHORIZATION_REQUIRED','ACCOUNT_NOT_FOUND') else 'RECONNECTING'
                with db:db.execute('INSERT INTO events VALUES(?,?,?)',(time.time_ns(),state,label))
                publish()
                if state=='ACCESS_REQUIRED':break
                until=min(mono_end,time.monotonic()+min(60,2**min(streak,6)))
                while time.monotonic()<until and not (out/'STOP').exists():
                    time.sleep(min(5,max(0,until-time.monotonic())));publish()
        if state!='ACCESS_REQUIRED':state='STOPPED' if (out/'STOP').exists() else 'COMPLETED'
    except BaseException:
        state='INTERRUPTED';publish();raise
    finally:
        stop_event.set();history.join(timeout=25);calibration.join(timeout=16)
        summary=publish()
        (out/'assessment.md').write_text('# Official FX research collection\n\n'+json.dumps(summary,indent=2)+'\n',encoding='utf-8')
        db.close()


def launch(out,hours=24,days=90,config=None):
    config=config or p.credentials()
    p.check(config,instruments=UNIVERSE)
    if (out/'prices.sqlite').exists():raise ValueError('Study already exists; use a new folder')
    out.mkdir(parents=True,exist_ok=True)
    command=[sys.executable,str(Path(__file__).resolve()),'record-pipe','--out',str(out.resolve()),'--hours',str(hours),'--history-days',str(days)]
    with (out/'worker-output.log').open('wb') as stdout,(out/'worker-error.log').open('wb') as stderr:
        child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        child.stdin.write((json.dumps(config)+'\n').encode());child.stdin.close()
    atomic_json(out/'worker.json',{'pid':child.pid,'provider':'OANDA practice','hours':hours,'history_days':days,
                                 'credentials_persisted':False,'orders_enabled':False})
    print('Started official FX research collection; credentials remain in process memory only.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('launch','record-pipe'))
    parser.add_argument('--out',type=Path,default=ROOT/'official-feed-study')
    parser.add_argument('--hours',type=float,default=24)
    parser.add_argument('--history-days',type=int,default=90)
    args=parser.parse_args()
    if not 1<=args.hours<=48 or not 30<=args.history_days<=180:parser.error('hours must be 1..48; historical days 30..180')
    try:
        if args.action=='launch':launch(args.out,args.hours,args.history_days)
        else:record(args.out,args.hours,args.history_days,p.pipe_credentials())
    except (HTTPError,URLError,OSError,TimeoutError,ValueError,RuntimeError) as error:
        print('Study setup failed: '+p.error_label(error));raise SystemExit(1)
