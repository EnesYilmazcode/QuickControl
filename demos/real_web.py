"""Run an observed real-website GUI scenario through Windows MCP only.

Plan: {"setup": [], "phases": [{"actions": [{"name": "Type",
"arguments": {"loc": [300, 300], "text": "example"}}], "observe": true}]}.
Launch/setup and human observation waits are excluded from GUI execution time.
No success is claimed without independent file verification or an explicit
evidence-backed review.json. Failed tool calls are never automatically retried.
"""
import argparse
import asyncio
import base64
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import shutil
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demos.layout import activate, assert_foreground
from demos.video import Recorder


def identify_window(title):
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    matches = []

    @callback
    def visit(handle, _):
        caption, classname = ctypes.create_unicode_buffer(2048), ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(handle, caption, 2048)
        user32.GetClassNameW(handle, classname, 256)
        if (user32.IsWindowVisible(handle) and classname.value == 'Chrome_WidgetWin_1'
                and title.casefold() in caption.value.casefold()):
            matches.append((handle, caption.value))
        return True
    user32.EnumWindows(visit, 0)
    if len(matches) != 1:
        raise RuntimeError(f'Expected exactly one Chrome window matching {title!r}; found {len(matches)}')
    handle, caption = matches[0]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    if not user32.SetWindowPos(handle, 0, 60, 60, 1240, 900, 0x0040):
        raise ctypes.WinError(ctypes.get_last_error())
    activate(handle)
    return handle, caption


def foreground_title():
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    title = ctypes.create_unicode_buffer(2048)
    user32.GetWindowTextW(user32.GetForegroundWindow(), title, 2048)
    return title.value


async def gate(path, timeout=600):
    print('OBSERVE THEN CREATE:', path, flush=True)
    started = time.perf_counter()
    while not path.exists():
        if time.perf_counter()-started > timeout:
            raise TimeoutError(f'Observation gate timed out: {path}')
        await asyncio.sleep(.2)
    return time.perf_counter()-started


def load_plan(path):
    plan = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(plan.get('phases'), list) or not plan['phases']:
        raise ValueError('Plan needs nonempty phases array')
    for actions in [plan.get('setup', [])] + [p.get('actions', []) for p in plan['phases']]:
        if not isinstance(actions, list):
            raise ValueError('Actions must be a list')
        for action in actions:
            if not isinstance(action.get('name'), str) or not isinstance(action.get('arguments', {}), dict):
                raise ValueError('Action requires name and arguments object')
    return plan


def review_outcome(plan, review_path):
    verification = plan.get('verification', {})
    if verification.get('kind') == 'file':
        path = Path(verification['path'])
        actual = path.read_text(encoding=verification.get('encoding', 'utf-8-sig'))
        expected = verification['expected_text']
        return {'passed': actual == expected, 'status': 'verified', 'method': 'exact-saved-file',
                'path': str(path), 'actual': actual, 'expected': expected}
    if review_path.exists():
        review = json.loads(review_path.read_text(encoding='utf-8-sig'))
        if type(review.get('passed')) is not bool or not review.get('evidence') or not review.get('observed'):
            raise ValueError('review.json needs passed boolean, evidence and observed fields')
        return {'passed': review['passed'], 'status': 'reviewed', 'method': 'independent-review', **review}
    return {'passed': False, 'status': 'pending-review', 'method': 'none',
            'reason': 'A screenshot or title alone does not establish task success'}


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['baseline', 'rapid'], required=True)
    parser.add_argument('--url', required=True)
    parser.add_argument('--title', required=True, help='Unique observed Chrome window title substring')
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--record', action='store_true')
    parser.add_argument('--wait-review', action='store_true')
    parser.add_argument('--review-file', type=Path)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--chrome', default=r'C:\Program Files\Google\Chrome\Application\chrome.exe')
    parser.add_argument('--load-wait', type=float, default=2)
    parser.add_argument('--debug-port', type=int, help='Read-only independent verifier can attach to this isolated browser')
    args = parser.parse_args()
    if not args.url.startswith(('https://', 'http://')):
        parser.error('--url requires HTTP or HTTPS')
    output = ROOT/'artifacts'/'real-web'/(time.strftime('%Y%m%d-%H%M%S')+'-'+args.profile+'-'+uuid.uuid4().hex[:6])
    output.mkdir(parents=True)
    print('OUTPUT:', output, flush=True)
    review_path = args.review_file or output/'review.json'
    result = {'profile': args.profile, 'url': args.url, 'plan_path': str(args.plan),
              'passed': False, 'verification': {'status': 'not-started'}, 'calls': [],
              'scope': 'Real website GUI execution; startup, launch, setup, preflight and human observation waits excluded',
              'recording_scope': 'Uncut GUI attempt including any phase observation waits; original speed'}
    script = 'baseline_server.py' if args.profile == 'baseline' else 'rapid_server.py'
    params = StdioServerParameters(command=shutil.which('uv') or str(Path.home()/'.local/bin/uv.exe'),
        args=['run', '--python', '3.14', '--with', 'windows-mcp==0.8.8', 'python', str(ROOT/script), 'serve'])

    def persist():
        (output/'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    persist()
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            async def call(name, arguments=None, stage='setup'):
                start = time.perf_counter()
                response = await asyncio.wait_for(session.call_tool(name, arguments or {}), 120)
                error = bool(getattr(response, 'is_error', getattr(response, 'isError', False)))
                row = {'name': name, 'arguments': arguments or {}, 'stage': stage,
                       'elapsed_ms': round((time.perf_counter()-start)*1000, 3), 'is_error': error}
                row['text'] = [b.text for b in response.content if b.type == 'text']
                result['calls'].append(row)
                persist()
                if error:
                    raise RuntimeError(f'{name} returned MCP error; no action retried: {row["text"]}')
                return response

            async def screenshot(label):
                response = await call('Screenshot', {'display': [0]}, stage='observation')
                for index, block in enumerate(response.content):
                    if block.type == 'image':
                        suffix = '.jpg' if getattr(block, 'mimeType', getattr(block, 'mime_type', '')) == 'image/jpeg' else '.png'
                        path = output/(label+f'-{index}'+suffix)
                        path.write_bytes(base64.b64decode(block.data))
                        print('SCREENSHOT:', path, flush=True)

            recorder = None
            try:
                status = await call('ControlStatus')
                states = [json.loads(b.text).get('state') for b in status.content if b.type == 'text']
                if states != ['ready']:
                    raise RuntimeError(f'ControlStatus not ready: {states}')
                launch_args = ['--user-data-dir='+str(ROOT/'artifacts'/'real-browser'), '--no-first-run',
                               '--app='+args.url, '--window-position=60,60', '--window-size=1240,900']
                if args.debug_port:
                    launch_args += [f'--remote-debugging-port={args.debug_port}',
                                    f'--remote-allow-origins=http://localhost:{args.debug_port}']
                await call('App', {'mode': 'launch_executable', 'executable': args.chrome, 'args': launch_args})
                await asyncio.sleep(args.load_wait)
                handle, caption = identify_window(args.title)
                result['initial_title'] = caption
                await screenshot('before')
                if args.inspect:
                    await gate(output/'go')
                # Coordinates can be filled into the plan after reviewing before.png.
                plan = load_plan(args.plan)
                (output/'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
                for action in plan.get('setup', []):
                    assert_foreground(handle)
                    await call(action['name'], action.get('arguments', {}))
                if args.record:
                    recorder = Recorder(output/'0.mp4', (60, 60, 1240, 900), ffmpeg=args.ffmpeg)
                    await asyncio.to_thread(recorder.start)
                assert_foreground(handle)
                started, excluded = time.perf_counter(), 0
                for index, phase in enumerate(plan['phases']):
                    for action in phase.get('actions', []):
                        assert_foreground(handle)
                        await call(action['name'], action.get('arguments', {}), stage=f'phase-{index}')
                    if phase.get('observe') or phase.get('screenshot'):
                        before = time.perf_counter()
                        await screenshot(f'phase-{index}')
                        if phase.get('observe'):
                            await gate(output/f'phase-{index}-go')
                        excluded += time.perf_counter()-before
                ended = time.perf_counter()
                result.update(gui_ms=round((ended-started-excluded)*1000, 3),
                              attempt_wall_ms=round((ended-started)*1000, 3),
                              excluded_observation_ms=round(excluded*1000, 3),
                              final_foreground_title=foreground_title())
                await screenshot('after')
                await asyncio.sleep(.35)
                if recorder:
                    await asyncio.to_thread(recorder.stop)
                    recorder = None
                if args.wait_review and plan.get('verification', {}).get('kind') != 'file':
                    await gate(review_path)
                outcome = review_outcome(plan, review_path)
                result.update(passed=outcome['passed'], verification=outcome)
                persist()
                print('RESULT:', json.dumps(result, ensure_ascii=False), flush=True)
            except BaseException as error:
                result.update(passed=False, error=f'{type(error).__name__}: {error}')
                persist()
                raise
            finally:
                if recorder:
                    await asyncio.to_thread(recorder.stop)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
