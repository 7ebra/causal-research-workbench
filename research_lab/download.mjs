import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.dirname(fileURLToPath(import.meta.url));
const out=path.join(root,'data');
await fs.mkdir(out,{recursive:true});
const assets=['EURUSD','USDJPY','AUDUSD'];
const manifest={provider:'Yahoo Finance chart endpoint (unofficial public interface)',
  provenance:'External FX reference prices; NOT Quotex execution or OTC prices',
  retrieved_at:new Date().toISOString(),interval_seconds:300,assets:[]};
for(const asset of assets){
  const url=`https://query1.finance.yahoo.com/v8/finance/chart/${asset}=X?interval=5m&range=60d`;
  const r=await fetch(url,{signal:AbortSignal.timeout(30000)});
  if(!r.ok)throw new Error(`${asset}: HTTP ${r.status}`);
  const raw=await r.json();
  if(raw.chart.error)throw new Error(JSON.stringify(raw.chart.error));
  const d=raw.chart.result[0],q=d.indicators.quote[0],rows=[];
  let missing=0,unfinished=0;
  for(let i=0;i<d.timestamp.length;i++){
    const t=d.timestamp[i];
    if(t+300>Date.now()/1000){unfinished++;continue;}
    const values=[q.open[i],q.high[i],q.low[i],q.close[i]];
    if(!values.every(v=>Number.isFinite(v)&&v>0)){missing++;continue;}
    rows.push([t,...values]);
  }
  if(rows.length<1000)throw new Error(`${asset}: insufficient candles`);
  await fs.writeFile(path.join(out,`${asset}.csv`),'timestamp,open,high,low,close\n'+rows.map(x=>x.join(',')).join('\n')+'\n');
  await fs.writeFile(path.join(out,`${asset}.raw.json`),JSON.stringify(raw));
  const info={asset,url,candles:rows.length,missing_rows:missing,unfinished_rows:unfinished,
    first:new Date(rows[0][0]*1000).toISOString(),last:new Date(rows.at(-1)[0]*1000).toISOString()};
  manifest.assets.push(info);console.log(JSON.stringify(info));
}
await fs.writeFile(path.join(out,'manifest.json'),JSON.stringify(manifest,null,2));
