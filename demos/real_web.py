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
from urllib.parse import urlsplit
from urllib.request import urlopen

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


def verify_cdp(verification, port):
    """Observe this run's isolated browser; never enter task input through CDP.

    Expressions are reviewed scenario literals, not website-supplied code.
    throwOnSideEffect additionally makes Chrome reject mutating evaluation.
    Only a local target on the explicitly launched debug port is permitted.
    """
    import websocket
    if not port or not 1 <= port <= 65535:
        raise ValueError('CDP verification requires --debug-port for the isolated browser')
    expression = verification['expression']
    contains = verification['url_contains']
    expected = verification.get('expected', True)
    timeout = float(verification.get('timeout_seconds', 15))
    if not isinstance(expression, str) or not isinstance(contains, str) or not contains:
        raise ValueError('CDP verification requires a reviewed expression and nonempty URL filter')
    if not 0 < timeout <= 120:
        raise ValueError('CDP verification timeout must be between zero and 120 seconds')
    started, attempts, actual, last_error, observed_url = time.perf_counter(), 0, None, None, None
    while True:
        attempts += 1
        connection = None
        try:
            with urlopen(f'http://127.0.0.1:{port}/json/list', timeout=2) as response:
                targets = json.load(response)
            targets = [target for target in targets if target.get('type') == 'page'
                       and contains in target.get('url', '')]
            if len(targets) != 1:
                raise ValueError(f'Expected one matching isolated browser target; found {len(targets)}')
            target = targets[0]
            endpoint = target['webSocketDebuggerUrl']
            endpoint_parts = urlsplit(endpoint)
            if endpoint_parts.hostname not in ('localhost', '127.0.0.1', '::1') or endpoint_parts.port != port:
                raise ValueError('Verifier refuses a debugger outside the launched local port')
            connection = websocket.create_connection(endpoint, timeout=2, origin=f'http://localhost:{port}')
            connection.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {
                'expression': expression, 'returnByValue': True, 'throwOnSideEffect': True}}))
            while True:
                response = json.loads(connection.recv())
                if response.get('id') == 1:
                    break
            if response.get('error') or response.get('result', {}).get('exceptionDetails'):
                raise ValueError(json.dumps(response.get('error') or response['result']['exceptionDetails']))
            actual = response['result']['result'].get('value')
            observed_url, last_error = target['url'], None
            # JSON equality must preserve Boolean-vs-number distinctions.
            passed = json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)
            if passed:
                return {'passed': True, 'status': 'verified', 'method': 'read-only-cdp',
                        'actual': actual, 'expected': expected, 'observed_url': observed_url,
                        'expression': expression, 'attempts': attempts,
                        'elapsed_ms': round((time.perf_counter()-started)*1000, 3)}
        except Exception as error:
            last_error = f'{type(error).__name__}: {error}'
        finally:
            if connection:
                connection.close()
        if time.perf_counter()-started >= timeout:
            return {'passed': False, 'status': 'verification-timeout', 'method': 'read-only-cdp',
                    'actual': actual, 'expected': expected, 'observed_url': observed_url,
                    'expression': expression, 'attempts': attempts, 'last_error': last_error,
                    'elapsed_ms': round((time.perf_counter()-started)*1000, 3)}
        time.sleep(.1)


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
                if args.inspect:
                    await screenshot('launch')
                    await gate(output/'launch-go')
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
                settle = float(plan.get('settle_seconds', 0))
                if not 0 <= settle <= 30:
                    raise ValueError('settle_seconds must be between zero and 30')
                await asyncio.sleep(settle)
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
                input_ended = time.perf_counter()
                verification_kind = plan.get('verification', {}).get('kind')
                outcome = None
                if verification_kind == 'cdp':
                    outcome = await asyncio.to_thread(verify_cdp, plan['verification'], args.debug_port)
                elif verification_kind == 'file':
                    outcome = await asyncio.to_thread(review_outcome, plan, review_path)
                ended = time.perf_counter()
                result.update(gui_ms=round((ended-started-excluded)*1000, 3),
                              input_only_ms=round((input_ended-started-excluded)*1000, 3),
                              verification_ms=round((ended-input_ended)*1000, 3),
                              timing_end='independently verified outcome' if outcome else 'input completed; manual review outside timer',
                              attempt_wall_ms=round((ended-started)*1000, 3),
                              excluded_observation_ms=round(excluded*1000, 3),
                              final_foreground_title=foreground_title())
                await screenshot('after')
                await asyncio.sleep(.35)
                if recorder:
                    await asyncio.to_thread(recorder.stop)
                    recorder = None
                if args.wait_review and outcome is None:
                    await gate(review_path)
                if outcome is None:
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
