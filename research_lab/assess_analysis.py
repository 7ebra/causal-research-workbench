"""Read-only assessment of a bounded public-analysis source study."""
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import sys


def assess(folder):
    folder=folder.resolve();status=json.loads((folder/'status.json').read_text())
    if status['state'] not in ('COMPLETED','STOPPED','BLOCKED','INTERRUPTED'):
        raise ValueError('Source study has not ended')
    db=sqlite3.connect((folder/'analysis.sqlite').as_uri()+'?mode=ro',uri=True)
    db.execute('pragma temp_store=memory')
    rows=db.execute('SELECT url,first_seen,published_original,eligibility,context,http_metadata FROM articles ORDER BY id').fetchall()
    db.close()
    result={'state':status['state'],'article_versions':len(rows),'distinct_articles':len({r[0] for r in rows}),
        'classifications':dict(Counter(r[3] for r in rows)),'fetch_errors':status.get('fetch_errors'),
        'fetch_attempts':status.get('fetch_attempts'),'eligible_forecasts':0,'verified_outcomes':0,
        'training_performed':False,'orders_enabled':False,'articles':[
            {'url':url,'first_observed_utc':datetime.fromtimestamp(seen,timezone.utc).isoformat(),
             'published_original':published,'eligibility':eligibility,'context':json.loads(context),
             'http_metadata':json.loads(headers)} for url,seen,published,eligibility,context,headers in rows],
        'limits':['Public pages may be cached; server modification dates are not article publication times.',
                  'Captured lexical context is not an automatic semantic forecast classifier.',
                  'No matching post-observation live OANDA quotes are attached, so no performance can be scored.',
                  'Archive observations cannot be backdated into earlier trading decisions.']}
    return result


if __name__=='__main__':
    folder=Path(sys.argv[1]);result=assess(folder)
    with (folder/'assessment.json').open('x',encoding='utf-8') as file:json.dump(result,file,indent=2)
    lines=['# Public analysis source assessment','',
        f"State: {result['state']}. Article versions: {result['article_versions']}. Fetch errors: {result['fetch_errors']}.",
        '', 'No verified forecast or trading outcome. No fitting or orders occurred.', '']
    lines += ['- '+limit for limit in result['limits']]
    with (folder/'assessment.md').open('x',encoding='utf-8') as file:file.write('\n'.join(lines)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='articles'},indent=2))
