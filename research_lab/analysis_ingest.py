"""Bounded public-analysis provenance and lexical context capture; no trading."""
import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urljoin,urlsplit
from urllib.request import HTTPRedirectHandler,Request,build_opener
from urllib.robotparser import RobotFileParser

ROOT=Path(__file__).resolve().parent
HOME='https://www.marketpulse.com/'
PAIRS=('EUR_USD','USD_JPY','AUD_USD','CAD_JPY')
MAX_BYTES=2_000_000
MAX_AGE=7200
VOID_TAGS=('meta','link','img','br','hr','input','source','wbr','area','base','embed','param','track')


def allowed_url(url):
    p=urlsplit(url)
    return p.scheme=='https' and p.hostname=='www.marketpulse.com' and not p.username and not p.password and p.port in (None,443)


class Redirects(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not allowed_url(newurl):raise ValueError('Public-source redirect outside allowed host')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def fetch(url):
    if not allowed_url(url):raise ValueError('Unexpected public source')
    request=Request(url,headers={'User-Agent':'PatternLab-public-research/1.0','Cache-Control':'no-cache'})
    with build_opener(Redirects()).open(request,timeout=15) as response:
        payload=response.read(MAX_BYTES+1)
        if len(payload)>MAX_BYTES:raise ValueError('Source response exceeded bounded size')
        return {'url':response.url,'received_at':time.time(),'body':payload.decode('utf-8'),
            'response_sha256':hashlib.sha256(payload).hexdigest(),
            'headers':{k:response.headers.get(k) for k in ('Date','Last-Modified','Cache-Control','Age')}}


class Page(HTMLParser):
    def __init__(self):
        super().__init__();self.json_scripts=[];self.active_json=None;self.links=[];self.link=None
        self.depth=0;self.rich_depth=None;self.analysis_text=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='script' and a.get('type')=='application/ld+json':self.active_json=[]
        if tag=='a' and a.get('href'):self.link=[urljoin(HOME,a['href']),[]]
        if tag not in VOID_TAGS:self.depth+=1
        if tag=='div' and 'rich-text' in a.get('class','').split():self.rich_depth=self.depth
    def handle_data(self,text):
        if self.active_json is not None:self.active_json.append(text)
        if self.link is not None:self.link[1].append(text)
        if self.rich_depth is not None:self.analysis_text.append(text)
    def handle_endtag(self,tag):
        if tag in VOID_TAGS:return
        if tag=='script' and self.active_json is not None:
            self.json_scripts.append(''.join(self.active_json));self.active_json=None
        if tag=='a' and self.link is not None:
            url,parts=self.link;self.links.append((url,' '.join(' '.join(parts).split())));self.link=None
        if tag=='div' and self.rich_depth==self.depth:self.rich_depth=None
        self.depth=max(0,self.depth-1)


def objects(value):
    if isinstance(value,list):
        for item in value:yield from objects(item)
    elif isinstance(value,dict):
        yield value
        if '@graph' in value:yield from objects(value['@graph'])


def parsed_article(body):
    p=Page();p.feed(body)
    articles=[]
    for script in p.json_scripts:
        try:data=json.loads(script)
        except json.JSONDecodeError:continue
        for record in objects(data):
            kinds=record.get('@type',[]);kinds=[kinds] if isinstance(kinds,str) else kinds
            if any(k in ('NewsArticle','Article','BlogPosting') for k in kinds):articles.append(record)
    if len(articles)!=1:raise ValueError('Expected one unambiguous article metadata object')
    record=articles[0];title=str(record.get('headline','')).strip()
    if not title:raise ValueError('Article headline missing')
    published_raw=record.get('datePublished');published=None
    if published_raw:
        date=datetime.fromisoformat(published_raw.replace('Z','+00:00'))
        if date.tzinfo is None:raise ValueError('Publication timezone is missing')
        published=date.timestamp()
    author=record.get('author',[]);author=[author] if isinstance(author,dict) else author
    text=' '.join(' '.join(p.analysis_text).split())
    if not text:raise ValueError('Analysis body unavailable')
    return {'headline':title,'published_at':published,'published_original':published_raw,
            'modified_original':record.get('dateModified'),'authors':[a.get('name') for a in author if isinstance(a,dict)],
            'text':text}


def lexical_context(title,text):
    combined=title+' '+text
    pairs=sorted({a+'_'+b for a,b in re.findall(r'\b(USD|EUR|GBP|JPY|AUD|CAD|CHF|NZD)\s*/\s*(USD|EUR|GBP|JPY|AUD|CAD|CHF|NZD)\b',combined) if a!=b})
    return {'instruments':pairs,'matched_recording_instruments':[p for p in pairs if p in PAIRS],
        'bullish_language':bool(re.search(r'\b(bullish|uptrend)\b',combined,re.I)),
        'bearish_language':bool(re.search(r'\b(bearish|downtrend)\b',combined,re.I)),
        'conditional_language':bool(re.search(r'\b(if|unless|may|could)\b',text,re.I)),
        'forecast_direction':None,'forecast_horizon':None,'needs_semantic_review':True,
        'method':'Fixed lexical context only; not a semantic forecast or learned sentiment model.'}


def database(file):
    db=sqlite3.connect(file)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''CREATE TABLE IF NOT EXISTS articles(id INTEGER PRIMARY KEY,url TEXT,first_seen REAL,last_seen REAL,
        published_at REAL,published_original TEXT,headline TEXT,authors TEXT,content_hash TEXT,response_hash TEXT,
        http_metadata TEXT,context TEXT,eligibility TEXT,excerpt TEXT);
      CREATE INDEX IF NOT EXISTS article_url ON articles(url,id);
      CREATE TABLE IF NOT EXISTS fetch_events(received_at REAL,url TEXT,state TEXT);''')
    return db


def capture(db,response):
    if not allowed_url(response['url']):raise ValueError('Unexpected capture source')
    article=parsed_article(response['body']); seen=response['received_at'];pub=article['published_at']
    context=lexical_context(article['headline'],article['text'])
    reason='PUBLICATION_UNKNOWN' if pub is None else 'FUTURE_OR_CLOCK_AMBIGUOUS' if pub>seen+5 else 'ARCHIVE_CONTEXT' if seen-pub>MAX_AGE else 'FRESH_CONTEXT_REQUIRES_REVIEW'
    if not context['matched_recording_instruments']:reason='UNMATCHED_INSTRUMENT_CONTEXT'
    canonical={k:article[k] for k in ('headline','published_original','modified_original','authors','text')}
    digest=hashlib.sha256(json.dumps(canonical,sort_keys=True).encode()).hexdigest()
    latest=db.execute('SELECT id,content_hash FROM articles WHERE url=? ORDER BY id DESC LIMIT 1',(response['url'],)).fetchone()
    with db:
        if latest and latest[1]==digest:
            db.execute('UPDATE articles SET last_seen=? WHERE id=?',(seen,latest[0])); return 'UNCHANGED'
        db.execute('INSERT INTO articles(url,first_seen,last_seen,published_at,published_original,headline,authors,content_hash,response_hash,http_metadata,context,eligibility,excerpt) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (response['url'],seen,seen,pub,article['published_original'],article['headline'],json.dumps(article['authors']),
             digest,response['response_sha256'],json.dumps(response['headers']),json.dumps(context),reason,
             ' '.join(article['text'].split()[:20])))
    return 'REVISION' if latest else 'FIRST_SEEN'


def latest_urls(body):
    p=Page();p.feed(body); result=[]
    for url,title in p.links:
        if allowed_url(url) and '/markets/' in urlsplit(url).path and any(pair.replace('_','/') in title for pair in PAIRS) and url not in result:
            result.append(url)
    return result[:3]


def atomic_status(path,status):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(status,indent=2))
    for attempt in range(4):
        try:os.replace(temporary,path);return
        except PermissionError:
            if attempt==3:raise
            time.sleep(.1*(attempt+1))


def collect(out,minutes=90,poll_seconds=900,once=False):
    out.mkdir(parents=True,exist_ok=False)
    protocol={'minutes':minutes if not once else None,'mode':'ONE_SHOT_PREFLIGHT' if once else 'BOUNDED_SOURCE_READINESS',
              'poll_seconds':poll_seconds,'maximum_articles_per_poll':3,'maximum_fetch_attempts':30,
              'max_publication_age_seconds':MAX_AGE,'source':HOME,'orders_enabled':False,'training_enabled':False,
              'study_kind':'PUBLIC_ANALYSIS_READINESS','matched_quotes_available':False,
              'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2))
    db=database(out/'analysis.sqlite'); start=time.monotonic();deadline=start+minutes*60;errors=polls=0
    started=time.time(); status={};attempts=0;streak=0;blocked=False
    (out/'worker.json').write_text(json.dumps({'pid':os.getpid(),'started_at':started,'orders_enabled':False}))
    def request(url):
        nonlocal attempts
        if attempts>=30:raise RuntimeError('FETCH_BUDGET_EXHAUSTED')
        attempts+=1
        return fetch(url)
    rules=None
    try:
        while True:
            if (out/'STOP').exists():break
            polls+=1;errors_before=errors
            try:
                if rules is None:
                    robots=request(HOME+'robots.txt'); rules=RobotFileParser();rules.parse(robots['body'].splitlines())
                if not rules.can_fetch('PatternLab-public-research/1.0',HOME):raise RuntimeError('ROBOTS_ACCESS_BLOCKED')
                home=request(HOME)
                urls=latest_urls(home['body'])
                if not urls:raise ValueError('Homepage has no supported-pair article links')
                for url in urls:
                    if not rules.can_fetch('PatternLab-public-research/1.0',url):raise RuntimeError('ROBOTS_ACCESS_BLOCKED')
                    time.sleep(1)
                    try:
                        response=request(url);result=capture(db,response)
                        with db:db.execute('INSERT INTO fetch_events VALUES(?,?,?)',(response['received_at'],url,result))
                    except (OSError,ValueError,KeyError,TypeError) as error:
                        errors+=1
                        with db:db.execute('INSERT INTO fetch_events VALUES(?,?,?)',(time.time(),url,type(error).__name__))
            except (OSError,ValueError,KeyError,TypeError) as error:
                errors+=1
                with db:db.execute('INSERT INTO fetch_events VALUES(?,?,?)',(time.time(),HOME,type(error).__name__))
            except RuntimeError as error:
                blocked=True
                with db:db.execute('INSERT INTO fetch_events VALUES(?,?,?)',(time.time(),HOME,str(error)))
            streak=streak+1 if errors>errors_before else 0
            retry_seconds=min(60,2**min(streak,6)) if streak else poll_seconds
            status={'state':'RUNNING','updated_at':datetime.now(timezone.utc).isoformat(),'started_at':started,
                'ends_at':None if once else started+minutes*60,'polls':polls,'fetch_errors':errors,
                'fetch_attempts':attempts,'connection':'RECONNECTING' if streak else 'ONLINE','retry_seconds':retry_seconds,
                'article_versions':db.execute('SELECT count(*) FROM articles').fetchone()[0],
                'eligible_forecasts':0,'orders_enabled':False,'training_enabled':False,
                'limitation':'Lexical context capture, no semantic forecast scoring or matched live quote recorder.'}
            atomic_status(out/'status.json',status)
            if blocked or once or time.monotonic()>=deadline:break
            until=min(deadline,time.monotonic()+retry_seconds)
            while time.monotonic()<until and not (out/'STOP').exists():time.sleep(min(5,until-time.monotonic()))
            if time.monotonic()>=deadline:break
        status['state']='BLOCKED' if blocked else 'STOPPED' if (out/'STOP').exists() else 'COMPLETED'
        status['observed_end_at']=time.time();status['elapsed_monotonic_seconds']=time.monotonic()-start
        atomic_status(out/'status.json',status)
        print(json.dumps(status,indent=2))
    except BaseException:
        status.update(state='INTERRUPTED',updated_at=datetime.now(timezone.utc).isoformat(),
                      fetch_attempts=attempts,orders_enabled=False,training_enabled=False)
        atomic_status(out/'status.json',status)
        raise
    finally:db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--once',action='store_true');parser.add_argument('--minutes',type=int,default=90)
    args=parser.parse_args()
    if not 1<=args.minutes<=90:parser.error('minutes must be 1..90')
    collect(args.out,minutes=args.minutes,once=args.once)
