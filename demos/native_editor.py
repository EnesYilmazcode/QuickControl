"""Edit and save a synthetic JSON file in real Notepad, then verify its bytes."""
import argparse
import asyncio
import base64
import ctypes
from ctypes import wintypes
import json
import shutil
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from demos.layout import activate
EXPECTED='''{
  "project": "QuickControl",
  "greeting": "こんにちは 👋",
  "city": "Montréal",
  "enabled": true,
  "retry_limit": 3
}'''


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',choices=['baseline','rapid'],required=True)
    parser.add_argument('--inspect',action='store_true')
    parser.add_argument('--loc',type=int,nargs=2,default=[450,350])
    args=parser.parse_args()
    output=ROOT/'artifacts'/'native'/(time.strftime('%Y%m%d-%H%M%S')+'-'+args.profile)
    output.mkdir(parents=True)
    path=output/('quickcontrol-demo-'+output.name+'.json')
    path.write_text('{"draft": "replace this value"}',encoding='utf-8')
    script='baseline_server.py' if args.profile=='baseline' else 'rapid_server.py'
    params=StdioServerParameters(command=shutil.which('uv') or str(Path.home()/'.local/bin/uv.exe'),
        args=['run','--python','3.14','--with','windows-mcp==0.8.8','python',str(ROOT/script),'serve'])
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            async def call(name,arguments):
                before=time.perf_counter()
                result=await session.call_tool(name,arguments)
                if getattr(result,'is_error',getattr(result,'isError',False)):
                    raise RuntimeError(str(result))
                return result,round((time.perf_counter()-before)*1000,3)
            await call('App',{'mode':'launch_executable','executable':r'C:\Windows\System32\notepad.exe','args':[str(path)]})
            await asyncio.sleep(2)
            user32=ctypes.WinDLL('user32',use_last_error=True)
            user32.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
            callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
            user32.EnumWindows.argtypes=[callback,wintypes.LPARAM]
            matches=[]
            @callback
            def visit(handle,_):
                title=ctypes.create_unicode_buffer(1024)
                user32.GetWindowTextW(handle,title,1024)
                if path.name.lower() in title.value.lower(): matches.append(handle)
                return True
            user32.EnumWindows(visit,0)
            if len(matches)!=1: raise RuntimeError('Synthetic Notepad window could not be identified uniquely')
            activate(matches[0])
            before,_=await call('Screenshot',{})
            for block in before.content:
                if block.type=='image': (output/'before.png').write_bytes(base64.b64decode(block.data))
            print('OUTPUT:',output,flush=True)
            if args.inspect:
                gate=output/'go.json'
                print('INSPECT THEN WRITE LOC ARRAY TO:',gate,flush=True)
                deadline=time.monotonic()+300
                while True:
                    if time.monotonic()>deadline: raise TimeoutError('Observation gate timed out')
                    if gate.exists():
                        try:
                            args.loc=json.loads(gate.read_text(encoding='utf-8-sig'))
                            break
                        except (PermissionError, json.JSONDecodeError):
                            pass  # Only retry an unfinished observation-file read.
                    await asyncio.sleep(.2)
            user32=ctypes.WinDLL('user32',use_last_error=True)
            user32.GetForegroundWindow.restype=wintypes.HWND
            user32.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
            title=ctypes.create_unicode_buffer(1024)
            user32.GetWindowTextW(user32.GetForegroundWindow(),title,1024)
            if path.name.lower() not in title.value.lower():
                raise RuntimeError('Synthetic Notepad tab is not foreground; no text was entered')
            started=time.perf_counter()
            _,type_ms=await call('Type',{'loc':args.loc,'text':EXPECTED,'clear':True})
            _,save_ms=await call('Shortcut',{'shortcut':'ctrl+s'})
            deadline=time.monotonic()+3
            actual=''
            while True:
                actual=path.read_text(encoding='utf-8-sig').replace('\r\n','\n')
                if actual==EXPECTED or time.monotonic()>deadline: break
                await asyncio.sleep(.02)
            row={'profile':args.profile,'task':'native-json-edit','passed':actual==EXPECTED,
                 'verified_ms':round((time.perf_counter()-started)*1000,3),
                 'type_ms':type_ms,'save_ms':save_ms,'actual':actual,'expected':EXPECTED,
                 'scope':'Real Notepad GUI edit through verified saved file; startup, navigation and reasoning excluded'}
            (output/'results.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
            print('RESULT:',json.dumps(row,ensure_ascii=False),flush=True)
            after,_=await call('Screenshot',{})
            for block in after.content:
                if block.type=='image': (output/'after.png').write_bytes(base64.b64decode(block.data))
            if not row['passed']: raise RuntimeError('Saved file did not match; input was not retried')
            await call('Shortcut',{'shortcut':'ctrl+w'})


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
