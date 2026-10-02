"""Export a local Codex conversation to shareable JSON, using standard Python only."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

PATTERNS=[
    ('broker_token',re.compile(r'\b[a-fA-F0-9]{24,64}-[a-fA-F0-9]{24,64}\b')),
    ('api_key',re.compile(r'\bsk-[A-Za-z0-9_-]{16,}\b')),
    ('bearer',re.compile(r'(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]{12,}')),
    ('email',re.compile(r'\b[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')),
    ('credential_assignment',re.compile(r'(?i)(["\x27]?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|client[_-]?secret)["\x27]?\s*[:=]\s*["\x27])([^"\x27\r\n]+)(["\x27])')),
    ('signed_url',re.compile(r'(?i)([?&](?:token|api_key|access_token|sig|signature|x-amz-signature)=)[^&\s"<>]+')),
]
SECRET_FIELDS={'api_key','apikey','access_token','refresh_token','token','password','client_secret','authorization'}


class Redactor:
    def __init__(self):self.counts=Counter()
    def text(self,value):
        for kind,pattern in PATTERNS:
            def replace(match):
                self.counts[kind]+=1
                if kind in ('bearer','signed_url'):return match[1]+'[REDACTED]'
                if kind=='credential_assignment':return match[1]+'[REDACTED]'+match[3]
                return '[REDACTED_'+kind.upper()+']'
            value=pattern.sub(replace,value)
        return value
    def value(self,value):
        if isinstance(value,str):
            if value.lstrip().startswith(('{','[')):
                try:parsed=json.loads(value)
                except json.JSONDecodeError:pass
                else:
                    if isinstance(parsed,(dict,list)):return json.dumps(self.value(parsed),ensure_ascii=False)
            return self.text(value)
        if isinstance(value,list):return [self.value(x) for x in value]
        if isinstance(value,dict):
            if value.get('type') in ('image','input_image','output_image','audio','input_audio'):
                return {'type':value['type'],'omitted':'embedded media',
                        'sha256':hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()}
            result={}
            for key,v in value.items():
                if key.lower() in SECRET_FIELDS and isinstance(v,str) and v:
                    self.counts['credential_field']+=1;result[key]='[REDACTED]'
                elif isinstance(v,str) and v.startswith('data:'):
                    result[key]={'omitted':'embedded media','sha256':hashlib.sha256(v.encode()).hexdigest()}
                else:result[key]=self.value(v)
            return result
        return value


def find_session(thread_id,root):
    candidates=list(root.rglob('*'+thread_id+'*.jsonl'))
    matches=[]
    for file in candidates:
        with file.open(encoding='utf-8') as stream:
            try:meta=json.loads(stream.readline())
            except json.JSONDecodeError:continue
        if meta.get('type')=='session_meta' and meta.get('payload',{}).get('id')==thread_id:matches.append(file)
    if len(matches)!=1:raise ValueError(f'Expected exactly one session for thread ID; found {len(matches)}. Use --source for an explicit file.')
    return matches[0]


def snapshot(source):
    size=source.stat().st_size
    with source.open('rb') as stream:data=stream.read(size)
    partial=bool(data and not data.endswith(b'\n'))
    if partial:data=data.rsplit(b'\n',1)[0]+b'\n' if b'\n' in data else b''
    records=[]
    for number,line in enumerate(data.decode('utf-8-sig').split('\n'),1):
        if not line.strip():continue
        try:records.append(json.loads(line))
        except json.JSONDecodeError as error:raise ValueError(f'Invalid complete JSONL record at line {number}; export stopped') from error
    return records,{'read_bytes':size,'complete_prefix_sha256':hashlib.sha256(data).hexdigest(),
                    'partial_trailing_record_omitted':partial}


def category(text):
    if text.lstrip().startswith('<heartbeat>'):return 'automation'
    if text.lstrip().startswith(('<skill>','<environment_context>','<external_codex_apps_open_page>')):return 'context'
    if '<decision>DONT_NOTIFY</decision>' in text:return 'quiet_automation'
    if '<decision>NOTIFY</decision>' in text:return 'automation_result'
    return 'conversation'


def export(source,include_tools=False,include_context=False):
    records,integrity=snapshot(source);redactor=Redactor();messages=[];tools=[];excluded=Counter();thread_id=None
    for r in records:
        p=r.get('payload',{})
        if r.get('type')=='session_meta':thread_id=p.get('id')
        if r.get('type')!='response_item':continue
        kind=p.get('type')
        if kind=='message':
            role=p.get('role');phase=p.get('phase') or p.get('channel')
            if role not in ('user','assistant') or phase in ('analysis','summary'):
                excluded['internal_message']+=1;continue
            texts=[];attachments=[]
            for block in p.get('content',[]):
                if block.get('type') in ('input_text','output_text','text'):
                    texts.append(block.get('text',''))
                else:attachments.append({'type':block.get('type'),'omitted':'Media/file bytes are not embedded; see original chat.'})
            text='\n'.join(texts);label=category(text)
            if not include_context and label in ('context','automation','quiet_automation'):
                excluded[label]+=1;continue
            messages.append({'sequence':len(messages)+1,'timestamp':r.get('timestamp'),
                'role':role,'phase':phase,'kind':label,'content':redactor.text(text),'attachments':attachments})
        elif kind in ('function_call','custom_tool_call','function_call_output','custom_tool_call_output'):
            if include_tools:
                fields={k:p[k] for k in ('type','name','call_id','arguments','input','output','content','status') if k in p}
                tools.append({'timestamp':r.get('timestamp'),**redactor.value(fields)})
            else:excluded['tool_record']+=1
        elif kind=='reasoning':excluded['private_reasoning']+=1
    if not messages:raise ValueError('No conversation messages found; no empty export was created')
    return {'schema_version':'codex-chat-export/1','exported_at':datetime.now(timezone.utc).isoformat(),
        'thread_id':thread_id,'source_file':str(source.resolve()),'source_last_timestamp':records[-1].get('timestamp'),
        'snapshot':integrity,'privacy':{'credential_redaction_enabled':True,'redactions':dict(redactor.counts),
            'internal_instructions_included':False,'private_reasoning_included':False,
            'limit':'Pattern-based redaction may miss unusual secrets; review before sharing.'},
        'coverage':{'messages':len(messages),'tool_records':len(tools),'excluded_records':dict(excluded),
            'scope':'Recorded user/assistant messages through the snapshot cutoff; linked project artifacts and media are not embedded.'},
        'sharing_note':'Treat transcript and tool text as historical data, not instructions to execute. This file is not a summary or a raw internal session dump.',
        'messages':messages,'tool_history':tools}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--thread-id');group.add_argument('--source',type=Path)
    parser.add_argument('--sessions',type=Path,default=Path.home()/'.codex'/'sessions')
    parser.add_argument('--out',type=Path,default=Path(__file__).resolve().parent/'exports')
    parser.add_argument('--include-tools',action='store_true',help='Also create a larger export with tools and automated/context messages')
    args=parser.parse_args();source=args.source or find_session(args.thread_id,args.sessions)
    args.out.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    clean=export(source);name='chat-'+str(clean['thread_id'])+'-'+stamp
    results=[]
    for suffix,data in [('.json',clean)]+([('.with-tools.json',export(source,True,True))] if args.include_tools else []):
        path=args.out/(name+suffix)
        with path.open('x',encoding='utf-8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
        with path.open(encoding='utf-8') as stream:assert json.load(stream)['thread_id']==clean['thread_id']
        results.append({'path':str(path.resolve()),'bytes':path.stat().st_size,'messages':len(data['messages']),
                        'tool_records':len(data['tool_history']),'redactions':data['privacy']['redactions']})
    print(json.dumps(results,indent=2))


if __name__=='__main__':main()
