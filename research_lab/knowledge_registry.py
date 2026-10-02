"""Version short research notes with actual observation times; no web access."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit


def utc_time(value):
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:raise ValueError('UTC offset required')
    return parsed.timestamp()


def connect(path):
    db=sqlite3.connect(path)
    db.execute('''CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY,url TEXT,category TEXT,
        observed_at REAL,published_at REAL,available_at REAL,summary TEXT,note_sha256 TEXT)''')
    return db


def record(db,note,observed_at=None):
    url=note['url']; parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in ('www.oanda.com','www.marketpulse.com') or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError('Only reviewed public OANDA/MarketPulse HTTPS hosts allowed')
    summary=note['summary'].strip()
    if not summary or len(summary.split())>100:raise ValueError('Use a short original research summary')
    category=note['category']
    if category not in ('education','commentary','access_description'):raise ValueError('Unknown note category')
    observed=observed_at if observed_at is not None else datetime.now(timezone.utc).timestamp()
    publication=utc_time(note['published_at']) if note.get('published_at') else None
    # Source publication is never substituted for our actual first observation.
    available=max(observed,publication) if publication is not None else observed
    digest=hashlib.sha256(json.dumps({'url':url,'category':category,'summary':summary,
        'published_at':publication},sort_keys=True).encode()).hexdigest()
    latest=db.execute('SELECT note_sha256 FROM notes WHERE url=? ORDER BY id DESC LIMIT 1',(url,)).fetchone()
    if latest and latest[0]==digest:return False
    with db:db.execute('INSERT INTO notes(url,category,observed_at,published_at,available_at,summary,note_sha256) VALUES(?,?,?,?,?,?,?)',
                       (url,category,observed,publication,available,summary,digest))
    return True


def available_notes(db,asof):
    rows=db.execute('''SELECT url,summary,note_sha256,available_at FROM notes n WHERE available_at<=?
        AND id=(SELECT max(id) FROM notes WHERE url=n.url AND available_at<=?) ORDER BY id''',(asof,asof)).fetchall()
    return [dict(url=u,summary=s,note_sha256=h,available_at=a) for u,s,h,a in rows]


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--notes',type=Path,required=True)
    parser.add_argument('--database',type=Path,required=True)
    args=parser.parse_args()
    notes=json.loads(args.notes.read_text(encoding='utf-8'))
    db=connect(args.database)
    try:
        added=sum(record(db,note) for note in notes)
        print(json.dumps({'new_note_versions':added,'stored_notes':db.execute('SELECT count(*) FROM notes').fetchone()[0],
                          'training_performed':False,'raw_pages_archived':False}))
    finally:db.close()
