// Read-only public reference prices. No broker or account connection.
const assets=['EURUSD','USDJPY','AUDUSD'];
export async function collectSnapshots(){
const result=[];
for(const asset of assets){
  const started=Date.now()/1000;
  try{
    const url=`https://query1.finance.yahoo.com/v8/finance/chart/${asset}=X?interval=5m&range=5d`;
    const response=await fetch(url,{signal:AbortSignal.timeout(15000),headers:{'Cache-Control':'no-cache'}});
    if(!response.ok)throw Error(`HTTP ${response.status}`);
    const raw=await response.json(),d=raw.chart?.result?.[0];
    if(!d)throw Error(JSON.stringify(raw.chart?.error||'Missing chart result'));
    result.push({asset,request_started:started,received_at:Date.now()/1000,url,meta:d.meta,
      timestamps:d.timestamp,quote:d.indicators?.quote?.[0]});
  }catch(error){const cause=error.cause?.code;result.push({asset,request_started:started,received_at:Date.now()/1000,error:error.message+(cause?' ['+cause+']':'')});}
}
return result;
}
if(process.argv[1] && import.meta.url === (await import('node:url')).pathToFileURL(process.argv[1]).href){
process.stdout.write(JSON.stringify(await collectSnapshots()));
}
