"""Read-only OANDA practice-feed readiness study. No order endpoints."""
import argparse
from datetime import datetime, timezone
import getpass
import json
import math
import os
from pathlib import Path
import re
import socket
import sqlite3
import ssl
import statistics
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler

ROOT=Path(__file__).resolve().parent.parent
API='https://api-fxpractice.oanda.com'
STREAM='https://stream-fxpractice.oanda.com'
INSTRUMENTS=('EUR_USD','USD_JPY','AUD_USD')


def validate_credentials(account,token):
    if not re.fullmatch(r'[0-9-]{6,64}',account):raise ValueError('Invalid account ID format')
    if not re.fullmatch(r'[A-Za-z0-9._~+/=-]{20,1024}',token):raise ValueError('Invalid token format')
    return {'account':account,'token':token}


def credentials():
    if not sys.stdin.isatty():raise RuntimeError('Run setup in an interactive PowerShell terminal so the token stays hidden')
    account=input('OANDA PRACTICE account ID: ').strip()
    token=getpass.getpass('OANDA API token (hidden; memory only; never paste into chat): ').strip()
    return validate_credentials(account,token)


def pipe_credentials():
    data=json.loads(sys.stdin.buffer.readline(16384))
    return validate_credentials(data['account'],data['token'])


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise HTTPError(req.full_url,code,'Redirect refused',headers,fp)


def open_readonly(url,token,timeout=15):
    parts=urlsplit(url)
    if parts.scheme!='https' or parts.hostname not in ('api-fxpractice.oanda.com','stream-fxpractice.oanda.com') or parts.port not in (None,443):
        raise ValueError('Only official HTTPS practice endpoints are allowed')
    account_price=re.fullmatch(r'/v3/accounts/[^/]+/pricing(?:/stream)?',parts.path)
    instrument_candles=re.fullmatch(r'/v3/instruments/(?:EUR_USD|USD_JPY|AUD_USD|CAD_JPY)/candles',parts.path)
    if not account_price and not instrument_candles:
        raise ValueError('Unsupported read-only endpoint')
    request=Request(url,headers={'Authorization':'Bearer '+token,'Accept-Datetime-Format':'RFC3339','User-Agent':'PatternLab-readonly/1'},method='GET')
    opener=build_opener(NoRedirect(),HTTPSHandler(context=ssl.create_default_context()))
    return opener.open(request,timeout=timeout)


def error_label(error):
    if isinstance(error,HTTPError):
        return 'AUTHORIZATION_REQUIRED' if error.code in (401,403) else 'ACCOUNT_NOT_FOUND' if error.code==404 else f'HTTP_{error.code}'
    if isinstance(error,(URLError,TimeoutError,socket.timeout,OSError)):
        return 'NETWORK_OR_TLS_ERROR'
    return 'INVALID_FEED_MESSAGE'


def epoch_ns(value):
    match=re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?Z',value)
    if not match:raise ValueError('Invalid UTC provider timestamp')
    dt=datetime.strptime(match[1],'%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
    return int(dt.timestamp())*1_000_000_000+int((match[2] or '').ljust(9,'0'))


def normalize(message,received_ns,allowed=INSTRUMENTS):
    if not isinstance(message,dict):raise ValueError('Invalid feed object')
    if message.get('type')!='PRICE':return None
    instrument=message.get('instrument')
    if instrument not in allowed:raise ValueError('Unexpected instrument')
    timestamp=epoch_ns(message['time'])
    bid=float(message['bids'][0]['price']);ask=float(message['asks'][0]['price'])
    if not all(math.isfinite(v) and v>0 for v in (bid,ask)) or ask<bid:
        raise ValueError('Invalid bid/ask prices')
    age=(received_ns-timestamp)/1e9
    if age < -5:raise ValueError('Provider timestamp is too far ahead of the local clock')
    return {'instrument':instrument,'source_time':message['time'],'source_ns':timestamp,
            'received_ns':received_ns,'bid':bid,'ask':ask,'mid':(bid+ask)/2,
            'spread':ask-bid,'age_seconds':age,'tradeable':int(message.get('status')=='tradeable')}


def check(config=None,instruments=INSTRUMENTS):
    config=config or credentials()
    path=API+'/v3/accounts/'+quote(config['account'],safe='')+'/pricing?instruments='+','.join(instruments)
    with open_readonly(path,config['token']) as response:
        messages=json.load(response)['prices']
    received=time.time_ns()
    prices=[normalize(dict(m,type='PRICE'),received,allowed=instruments) for m in messages]
    if {price['instrument'] for price in prices if price}!=set(instruments):
        raise ValueError('Practice pricing did not return every requested instrument')
    print(json.dumps({'practice_read_access':'VERIFIED','instruments':[p['instrument'] for p in prices if p],
                      'sample_count':len(prices),'orders_enabled':False}))


def atomic_json(path,value):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,indent=2),encoding='utf-8');os.replace(temporary,path)


def public_status(db,start,end,state,errors,recoveries,last_message_ns):
    instruments={}
    for instrument in INSTRUMENTS:
        stats=db.execute('SELECT count(*),avg(age_seconds),max(age_seconds),max(received_ns) FROM prices WHERE instrument=?',(instrument,)).fetchone()
        instruments[instrument]={'samples':stats[0],'mean_age_seconds':stats[1],'max_age_seconds':stats[2],
                                 'last_observed_ns':stats[3]}
    return {'mode':'READ_ONLY_PRACTICE_FEED','state':state,'started_at':datetime.fromtimestamp(start,timezone.utc).isoformat(),
            'ends_at':datetime.fromtimestamp(end,timezone.utc).isoformat(),'updated_at':datetime.now(timezone.utc).isoformat(),
            'errors':errors,'recoveries':recoveries,'last_message_ns':last_message_ns,'instruments':instruments,
            'orders_enabled':False,'model_training_enabled':False,
            'limitation':'Source-to-receipt age is not network round-trip time. OANDA quotes do not establish Quotex settlement prices.'}


def record(out,minutes,config=None):
    config=config or credentials()
    out.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(out/'prices.sqlite');db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''CREATE TABLE IF NOT EXISTS prices(instrument TEXT,source_time TEXT,source_ns INTEGER,received_ns INTEGER,
      bid REAL,ask REAL,mid REAL,spread REAL,age_seconds REAL,tradeable INTEGER,PRIMARY KEY(instrument,source_ns));
      CREATE TABLE IF NOT EXISTS events(observed_ns INTEGER,kind TEXT,details TEXT);
      CREATE TABLE IF NOT EXISTS run(started REAL,ends REAL);''')
    if db.execute('SELECT count(*) FROM run').fetchone()[0]:
        db.close();raise ValueError('Readiness run already exists; choose a new output folder')
    start=time.time();end=start+minutes*60
    with db:db.execute('INSERT INTO run VALUES(?,?)',(start,end))
    state='CONNECTING';errors=recoveries=streak=0;last_message_ns=None;next_publish=0
    url=STREAM+'/v3/accounts/'+quote(config['account'],safe='')+'/pricing/stream?instruments='+','.join(INSTRUMENTS)+'&snapshot=true'
    try:
        while time.time()<end and not (out/'STOP').exists():
            try:
                with open_readonly(url,config['token'],timeout=15) as response:
                    if streak:recoveries+=1
                    streak=0;state='ONLINE'
                    with db:db.execute('INSERT INTO events VALUES(?,?,?)',(time.time_ns(),'CONNECTED','Official practice stream'))
                    atomic_json(out/'status.json',public_status(db,start,end,state,errors,recoveries,last_message_ns))
                    while time.time()<end and not (out/'STOP').exists():
                        line=response.readline()
                        if not line:raise URLError('Stream ended')
                        received=time.time_ns();message=json.loads(line)
                        if not isinstance(message,dict):raise ValueError('Invalid stream object')
                        if message.get('type') not in ('HEARTBEAT','PRICE'):raise ValueError('Unknown stream message type')
                        last_message_ns=received
                        price=normalize(message,received)
                        if price:
                            with db:db.execute('INSERT OR IGNORE INTO prices VALUES(?,?,?,?,?,?,?,?,?,?)',tuple(price.values()))
                        if time.monotonic()>=next_publish:
                            atomic_json(out/'status.json',public_status(db,start,end,state,errors,recoveries,last_message_ns))
                            next_publish=time.monotonic()+10
            except (HTTPError,URLError,TimeoutError,OSError,ValueError,KeyError,IndexError,TypeError) as error:
                errors+=1;streak+=1;label=error_label(error)
                state='ACCESS_REQUIRED' if label in ('AUTHORIZATION_REQUIRED','ACCOUNT_NOT_FOUND') else 'RECONNECTING'
                with db:db.execute('INSERT INTO events VALUES(?,?,?)',(time.time_ns(),state,label))
                atomic_json(out/'status.json',public_status(db,start,end,state,errors,recoveries,last_message_ns))
                if state=='ACCESS_REQUIRED':break
                retry_end=min(end,time.time()+min(60,2**min(streak,6)))
                while time.time()<retry_end and not (out/'STOP').exists():
                    time.sleep(min(5,max(0,retry_end-time.time())))
                    atomic_json(out/'status.json',public_status(db,start,end,state,errors,recoveries,last_message_ns))
        if state!='ACCESS_REQUIRED':state='STOPPED' if (out/'STOP').exists() else 'COMPLETED'
        summary=public_status(db,start,end,state,errors,recoveries,last_message_ns)
        quality={}
        for instrument in INSTRUMENTS:
            ages=sorted(r[0] for r in db.execute('SELECT age_seconds FROM prices WHERE instrument=?',(instrument,)))
            quality[instrument]={'samples':len(ages),'p95_age_seconds':ages[min(len(ages)-1,int(len(ages)*.95))] if ages else None,
                'clock_ahead_samples':sum(age<-.5 for age in ages)}
        summary['quality']=quality
        summary['readiness']='REVIEW_REQUIRED'  # No automatic trading/model approval.
        atomic_json(out/'status.json',summary)
        (out/'assessment.md').write_text('# Official FX feed readiness\n\n'+json.dumps(summary,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'state':state,'samples':sum(v['samples'] for v in quality.values()),'orders_enabled':False}))
    finally:db.close()


def launch(out,minutes,config=None):
    config=config or credentials()
    check(config)
    if (out/'prices.sqlite').exists():raise ValueError('Readiness folder already exists; use a new output folder')
    out.mkdir(parents=True,exist_ok=True)
    command=[sys.executable,str(Path(__file__).resolve()),'record-pipe','--out',str(out.resolve()),'--minutes',str(minutes)]
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    with (out/'worker-output.log').open('wb') as stdout,(out/'worker-error.log').open('wb') as stderr:
        child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,creationflags=flags)
        child.stdin.write((json.dumps(config)+'\n').encode());child.stdin.close()
    atomic_json(out/'worker.json',{'pid':child.pid,'provider':'OANDA practice','duration_minutes':minutes,
                                 'credentials_persisted':False,'orders_enabled':False})
    print('Started read-only practice readiness collector. Credentials are held only in its memory.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('check','record','launch','record-pipe'))
    parser.add_argument('--out',type=Path,default=ROOT/'official-feed-readiness')
    parser.add_argument('--minutes',type=float,default=15)
    args=parser.parse_args()
    if not 1<=args.minutes<=120:parser.error('Readiness duration must be 1..120 minutes')
    try:
        if args.action=='check':check()
        elif args.action=='launch':launch(args.out,args.minutes)
        elif args.action=='record-pipe':record(args.out,args.minutes,pipe_credentials())
        else:record(args.out,args.minutes)
    except (HTTPError,URLError,TimeoutError,OSError,ValueError,RuntimeError) as error:
        print('Setup/check failed: '+error_label(error) if isinstance(error,(HTTPError,URLError,TimeoutError,OSError)) else str(error))
        raise SystemExit(1)
