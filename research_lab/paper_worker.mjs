// Quote reads run in the directly launched process; the Python engine only handles local data.
import {spawn} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createInterface} from 'node:readline';
import {collectSnapshots} from './collect_snapshot.mjs';

const root=path.dirname(fileURLToPath(import.meta.url));
const args=process.argv.slice(2),options={};
for(let i=0;i<args.length;i+=2){
  if(!['--python','--out','--hours'].includes(args[i])||!args[i+1])throw Error('Unsupported worker argument');
  options[args[i]]=args[i+1];
}
const python=options['--python']||path.join(process.env.USERPROFILE||'','.cache','codex-runtimes','codex-primary-runtime','dependencies','python','python.exe');
const out=options['--out']||path.join(root,'forward-pilot');
const child=spawn(python,[path.join(root,'forward.py'),'--stdin-feed','--out',out,'--hours',options['--hours']||'24'],
  {stdio:['pipe','pipe','pipe'],windowsHide:true});
child.stderr.on('data',chunk=>process.stderr.write(chunk));
child.on('error',()=>{console.error('Local paper engine could not start');process.exitCode=1;});
let fetching=false;
createInterface({input:child.stdout}).on('line',async line=>{
  let message;
  try{message=JSON.parse(line);}catch{process.stdout.write(line+'\n');return;}
  if(message.type==='SNAPSHOT_REQUEST'){
    if(fetching){console.error('Duplicate local snapshot request');child.kill();return;}
    fetching=true;
    try{
      const snapshots=await collectSnapshots();
      if(!child.stdin.destroyed)child.stdin.write(JSON.stringify(snapshots)+'\n');
    }catch{
      if(!child.stdin.destroyed)child.stdin.write(JSON.stringify(['EURUSD','USDJPY','AUDUSD'].map(asset=>({asset,error:'Direct quote collection failed',received_at:Date.now()/1000})))+'\n');
    }finally{fetching=false;}
  }else process.stdout.write(line+'\n');
});
child.on('exit',code=>{process.exitCode=code||0;});
process.on('SIGINT',()=>child.kill());
process.on('SIGTERM',()=>child.kill());
