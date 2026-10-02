"""Assess a finalized official-feed study; allow diagnostic historical fitting only."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import statistics
import audit_history


def percentile(values,fraction):return sorted(values)[min(len(values)-1,int((len(values)-1)*fraction))] if values else None


def assess(study):
    study=study.resolve(); status=json.loads((study/'status.json').read_text())
    if status['state']!='COMPLETED':raise ValueError('Study has not completed; assessment cannot authorize fitting')
    history=audit_history.audit(study)
    db=sqlite3.connect((study/'prices.sqlite').as_uri()+'?mode=ro',uri=True)
    db.execute('pragma temp_store=memory');db.execute('BEGIN')
    try:
        run=db.execute('SELECT started,ends,started_monotonic FROM run').fetchone()
        duration=run[1]-run[0]; begin=int(run[2]*1e9); end=begin+int(duration*1e9)
        valid=True; instruments={}
        for asset in audit_history.ASSETS:
            rows=db.execute('SELECT source_ns,received_ns,receipt_monotonic_ns,bid,ask,age_seconds FROM prices WHERE instrument=? ORDER BY receipt_monotonic_ns',(asset,)).fetchall()
            invalid=sum(not all(math.isfinite(v) for v in (bid,ask,age)) or bid<=0 or ask<bid
                        or abs(age-(wall-source)/1e9)>1e-8 for source,wall,mono,bid,ask,age in rows)
            monotonic=all(a[2]<=b[2] for a,b in zip(rows,rows[1:]))
            window=[r for r in rows if begin<=r[2]<end]
            ages=[r[5] for r in window]
            bins={int((r[2]-begin)//60e9) for r in window}
            instruments[asset]={'total_quotes':len(rows),'quotes_inside_monotonic_window':len(window),
                'observed_minute_bins':len(bins),'planned_minutes':int(duration//60),
                'minute_bin_coverage_fraction':len(bins)/(duration/60),
                'invalid_bid_ask':invalid,'receipt_monotonic_order_valid':monotonic,
                'raw_age_median_seconds':statistics.median(ages) if ages else None,
                'raw_age_p95_seconds':percentile(ages,.95),'negative_raw_age_samples':sum(a<0 for a in ages)}
            valid=valid and invalid==0 and monotonic and len(window)>=100
        messages=db.execute('SELECT received_ns,receipt_monotonic_ns,kind FROM messages ORDER BY receipt_monotonic_ns').fetchall()
        gaps=[{'resumed_receipt_utc':datetime.fromtimestamp(b[0]/1e9,timezone.utc).isoformat(),
               'gap_seconds':(b[1]-a[1])/1e9} for a,b in zip(messages,messages[1:]) if b[1]-a[1]>30e9]
        events=db.execute('SELECT observed_ns,kind,details FROM events ORDER BY observed_ns').fetchall()
        probes=[json.loads(line) for line in (study/'clock-probes.jsonl').read_text().splitlines() if line.strip()]
        offsets=[p['reference_ahead_of_local_median_seconds'] for p in probes if p.get('status')=='MEASURED']
        return {'created_at':datetime.now(timezone.utc).isoformat(),'collection_state':'COMPLETED',
            'history_integrity':history['integrity'],'historical_fitting_allowed':bool(valid),
            'strategy_approved':False,'orders_enabled':False,'edge_established':False,
            'duration_seconds':duration,'instruments':instruments,
            'heartbeat_count':sum(m[2]=='HEARTBEAT' for m in messages),'message_count':len(messages),
            'stream_gaps_over_30_seconds':gaps,'fetch_errors':status['errors'],'recoveries':status['recoveries'],
            'events':[{'at_ns':t,'kind':k,'detail':v} for t,k,v in events],
            'clock_probes':len(probes),'reference_offset_range_seconds':[min(offsets),max(offsets)] if offsets else None,
            'history':history,'limits':['Minute bins measure observations, not exact uptime.',
                'Raw source age includes clock disagreement; no guessed correction was applied.',
                'Missed prices remain gaps; stream sampling is not every market tick.',
                'Fitting permission applies only to historical diagnostics, not prospective verification or execution.']}
    finally:db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--study',type=Path,required=True)
    args=parser.parse_args()
    try:
        result=assess(args.study)
        with (args.study/'research-assessment.json').open('x',encoding='utf-8') as file:json.dump(result,file,indent=2)
        print(json.dumps({k:v for k,v in result.items() if k not in ('history','events')},indent=2))
    except ValueError as error:parser.exit(2,str(error)+'\n')
