"""Read-only local study health display. Never reads credentials."""
import ctypes
from ctypes import wintypes
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parent


def process_alive(pid,expected_start=None):
    if os.name!='nt':
        try:os.kill(pid,0);return True
        except ProcessLookupError:return False
        except PermissionError:return None
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return None if ctypes.get_last_error()==5 else False
    try:
        code=wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):return None
        if code.value!=259:return False
        if expected_start is not None:
            values=[wintypes.FILETIME() for _ in range(4)]
            kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
            if kernel.GetProcessTimes(handle,*(ctypes.byref(v) for v in values)):
                created=((values[0].dwHighDateTime<<32)|values[0].dwLowDateTime)/10_000_000-11644473600
                if abs(created-expected_start)>90:return False
        return True
    finally:kernel.CloseHandle(handle)


def health(status,alive,now):
    state=status.get('state','UNKNOWN')
    message=status.get('last_message_ns')
    age=now-message/1e9 if message else None
    if state in ('COMPLETED','STOPPED','ACCESS_REQUIRED','INTERRUPTED'):
        return state,age
    if alive is False:return 'PROCESS_NOT_RUNNING',age
    if state=='RECONNECTING':return 'RECONNECTING',age
    if state=='ONLINE' and age is not None and -5<=age<=30:return 'DATA_FLOWING',age
    return 'STARTING_OR_DATA_STALE',age


def inspect(root=ROOT):
    studies=[]
    for folder in root.glob('official-feed-study-*'):
        try:
            status=json.loads((folder/'status.json').read_text())
            start=datetime.fromisoformat(status['started_at'].replace('Z','+00:00')).timestamp()
            studies.append((start,folder,status))
        except (OSError,ValueError,KeyError):continue
    if not studies:
        print('No initialized official FX study was found. Run Start OANDA research.cmd locally.')
        return
    start,folder,status=max(studies,key=lambda item:item[0])
    worker=json.loads((folder/'worker.json').read_text())
    alive=process_alive(worker['pid'],start)
    result,age=health(status,alive,time.time())
    print('OANDA research status:',result)
    print('Collector process:', 'running' if alive else 'not running' if alive is False else 'identity unavailable')
    print('Last quote or heartbeat:',f'{max(0,age):.1f} seconds ago' if age is not None else 'not received yet')
    print('Last report:',status['updated_at'])
    total=0
    for asset,info in status.get('instruments',{}).items():
        total+=info['samples'];print(' ',asset,':',info['samples'],'quotes')
    print('Total quotes:',total)
    print('Connection errors:',status.get('errors',0),' Recoveries:',status.get('recoveries',0))
    previous_path=folder/'health-checkpoint.json'
    if previous_path.exists():
        previous=json.loads(previous_path.read_text())
        print('Additional quotes since your previous check:',total-previous['quotes'])
    previous_path.write_text(json.dumps({'checked_at':time.time(),'quotes':total}),encoding='utf-8')
    print('Study folder:',folder)
    print('RECONNECTING means automatic retries are active. PROCESS_NOT_RUNNING requires a local restart with credentials.')


if __name__=='__main__':inspect()
