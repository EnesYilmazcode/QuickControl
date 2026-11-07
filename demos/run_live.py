"""Run outcome-verified Chrome GUI tasks through baseline or rapid MCP tools."""
import argparse
import asyncio
import base64
import json
import shutil
from pathlib import Path
import sys
import subprocess
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demos.cases import FORM, EDIT_NEW, SEARCH
from demos.benchmark import fetch_result
from demos.video import Recorder
from demos.layout import position_fixture, assert_foreground

ROOT = Path(__file__).resolve().parents[1]


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['baseline', 'rapid'], required=True)
    parser.add_argument('--task', choices=['form', 'edit', 'search'], required=True)
    parser.add_argument('--rounds', type=int, default=1)
    parser.add_argument('--inspect', action='store_true', help='Pause after first screenshot until go file exists')
    parser.add_argument('--record', action='store_true')
    parser.add_argument('--strategy', choices=['pointer','keyboard'], default='pointer')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    if not 1 <= args.rounds <= 20:
        parser.error('--rounds must be between 1 and 20')
    if args.rounds > 1:
        # Isolate runs so a prior window/indicator teardown cannot contaminate
        # the next lease. Startup remains outside every measured task window.
        for index in range(args.rounds):
            command=[sys.executable, str(Path(__file__).resolve()), '--profile',args.profile,
                     '--task',args.task,'--strategy',args.strategy,'--port',str(args.port),
                     '--ffmpeg',args.ffmpeg]
            if args.record: command.append('--record')
            if args.inspect and index == 0: command.append('--inspect')
            await asyncio.to_thread(subprocess.run, command, check=True, cwd=ROOT)
        return
    output = ROOT/'artifacts'/'live'/(time.strftime('%Y%m%d-%H%M%S')+'-'+args.profile+'-'+args.task+'-'+args.strategy)
    output.mkdir(parents=True)
    print('OUTPUT:', output, flush=True)
    script = 'baseline_server.py' if args.profile == 'baseline' else 'rapid_server.py'
    params = StdioServerParameters(command=shutil.which('uv') or str(Path.home()/'.local/bin/uv.exe'),
        args=['run', '--python', '3.14', '--with', 'windows-mcp==0.8.8', 'python', str(ROOT/script), 'serve'])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            async def call(name, arguments=None):
                before = time.perf_counter()
                result = await session.call_tool(name, arguments or {})
                if getattr(result, 'is_error', getattr(result, 'isError', False)):
                    raise RuntimeError(str(result))
                return result, round((time.perf_counter()-before)*1000, 3)
            async def shot(path):
                result, _ = await call('Screenshot')
                for block in result.content:
                    if block.type == 'image':
                        path.write_bytes(base64.b64decode(block.data))
                return str(path)
            rows=[]
            for index in range(args.rounds):
                run = f'{args.profile}-{args.task}-{uuid.uuid4().hex[:10]}'
                url = f'http://127.0.0.1:{args.port}/?task={args.task}&run={run}'
                await call('App', {'mode':'launch_executable',
                    'executable':r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                    'args':['--app='+url, '--window-size=1240,900', '--window-position=60,60']})
                await asyncio.sleep(1.5)
                handle=position_fixture('QuickControl demo | '+run)
                await asyncio.sleep(.25)
                print('SCREENSHOT:', await shot(output/f'{index}-before.png'), flush=True)
                if args.inspect and index == 0:
                    gate=output/'go'
                    print('INSPECT THEN CREATE:', gate, flush=True)
                    deadline=time.monotonic()+300
                    while not gate.exists():
                        if time.monotonic()>deadline: raise TimeoutError('Observation gate timed out')
                        await asyncio.sleep(.2)
                recorder = None
                if args.record:
                    recorder=Recorder(output/f'{index}.mp4', (60,60,1240,900), ffmpeg=args.ffmpeg)
                    await asyncio.to_thread(recorder.start)
                assert_foreground(handle)
                started=time.perf_counter()
                calls=[]
                async def timed(name, arguments=None):
                    result, elapsed=await call(name, arguments)
                    calls.append({'name':name,'ms':elapsed})
                    return result
                try:
                    if args.task == 'form':
                        positions=[(350,264),(820,264),(350,350),(820,350),
                                   (350,436),(820,436),(350,522),(820,522)]
                        if args.strategy == 'keyboard':
                            if args.profile == 'rapid':
                                await timed('FillForm', {'loc':list(positions[0]), 'fields':list(FORM.values()), 'submit':True})
                            else:
                                await timed('Type', {'loc':list(positions[0]), 'text':'\t'.join(FORM.values())+'\t', 'press_enter':True})
                        else:
                            await timed('MultiEdit', {'locs':[[x,y,value] for (x,y),value in zip(positions,FORM.values())]})
                            await timed('Click', {'loc':[260,585]})
                    elif args.task == 'edit':
                        await timed('Type', {'loc':[450,300], 'text':EDIT_NEW, 'clear':True})
                        await timed('Click', {'loc':[280,465]})
                    else:
                        await timed('Type', {'loc':[400,265], 'text':SEARCH['query'], 'clear':True, 'press_enter':True})
                        await shot(output/f'{index}-results.png')
                        await timed('Shortcut', {'shortcut':'enter'})
                        await shot(output/f'{index}-ticket.png')
                        await timed('Type', {'loc':[450,460], 'text':SEARCH['note'], 'clear':True})
                        await timed('Shortcut', {'shortcut':'tab'})
                        await timed('Shortcut', {'shortcut':'enter'})
                    deadline=time.monotonic()+3
                    while True:
                        result=await asyncio.to_thread(fetch_result, f'http://127.0.0.1:{args.port}', run)
                        if result.get('submitted') or time.monotonic()>deadline: break
                        await asyncio.sleep(.02)
                    elapsed=round((time.perf_counter()-started)*1000,3)
                    row={'profile':args.profile,'task':args.task,'run':run,'strategy':args.strategy,
                         'verified_ms':elapsed, 'passed':result.get('passed',False),
                         'calls':calls, 'result':result,
                         'scope':'Warm GUI execution through exact saved outcome; excludes setup, navigation, model reasoning'}
                    rows.append(row)
                    (output/'results.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False),encoding='utf-8')
                    print('RESULT:',json.dumps(row,ensure_ascii=False),flush=True)
                    print('SCREENSHOT:',await shot(output/f'{index}-after.png'),flush=True)
                    await asyncio.sleep(.35)
                finally:
                    if recorder: await asyncio.to_thread(recorder.stop)
                if not row['passed']:
                    raise RuntimeError('Exact outcome failed; inspect evidence before another attempt')
                await call('Shortcut',{'shortcut':'alt+f4'})
            print('RESULTS:',output/'results.json',flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
