"""End-to-end agent benchmark: a real Claude agent, same prompt, different tool server.

    python bench/agent_bench.py --arms stock,qc --tasks sauce,todo --reps 2

Each run starts from a fresh state (new Chrome profile, or a fresh Calculator),
runs `claude -p` headless with only that arm's MCP server and no built-in tools,
then checks the outcome independently. Wall time includes model thinking,
tool calls and MCP startup is reported separately.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bench'))
from tasks import PORT, TASKS  # noqa: E402

UV = shutil.which('uv') or str(Path.home() / '.local/bin/uv.exe')
CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
OUT = ROOT / 'bench' / 'results'

PY = str(ROOT / 'bench' / '.venv' / 'Scripts' / 'python.exe')
ARMS = {
    'stock': ['-m', 'windows_mcp', 'serve'],
    'qc1': [str(ROOT / 'rapid_server.py'), 'serve'],
    'qc': [str(ROOT / 'agent_server.py'), 'serve'],
}

PREAMBLE = ('You are operating this Windows PC through the wm tools only. {where} '
            'Do the task yourself without asking questions. When finished, reply with one short '
            'line that includes any answer the task asks for.\n\nTask: ')


def kill_bench_chrome():
    subprocess.run(['powershell', '-NoProfile', '-Command',
                    "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                    "Where-Object { $_.CommandLine -like '*qc-bench-profile*' } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                   capture_output=True, timeout=60)


def foreground(title_part):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    @callback
    def visit(hwnd, _):
        buffer = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buffer, 512)
        if user32.IsWindowVisible(hwnd) and title_part.lower() in buffer.value.lower():
            found.append(hwnd)
        return True
    user32.EnumWindows(visit, 0)
    if found:
        # Alt tap lets SetForegroundWindow succeed from a background process.
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.ShowWindow(found[0], 9)
        user32.SetForegroundWindow(found[0])
    return bool(found)


def write_quiet_prefs(profile):
    # The demo store's public password trips Chrome's breach warning, which
    # blocks page input for every arm. A real user would dismiss it once.
    default = profile / 'Default'
    default.mkdir(parents=True, exist_ok=True)
    (default / 'Preferences').write_text(json.dumps({
        'credentials_enable_service': False,
        'profile': {'password_manager_enabled': False, 'password_manager_leak_detection': False},
        'safebrowsing': {'enabled': False}}), encoding='utf-8')


def start_calculator():
    subprocess.run(['taskkill', '/IM', 'CalculatorApp.exe', '/F'], capture_output=True)
    time.sleep(1)
    subprocess.Popen(['cmd', '/c', 'start', 'calculator:'])
    time.sleep(3)


def setup(task, run_dir):
    urls = task.get('urls') or ([task['url']] if 'url' in task else [])
    if urls:
        kill_bench_chrome()
        time.sleep(1)
        if task.get('calc'):
            start_calculator()
        profile = Path(tempfile.gettempdir()) / f'qc-bench-profile-{run_dir.name}'
        task['_profile'] = str(profile)
        write_quiet_prefs(profile)
        subprocess.Popen([CHROME, f'--user-data-dir={profile}', f'--remote-debugging-port={PORT}',
                          f'--remote-allow-origins=http://localhost:{PORT}', '--no-first-run',
                          '--no-default-browser-check', '--window-position=0,0',
                          '--disable-features=PasswordLeakDetection,AutofillServerCommunication',
                          '--password-store=basic', '--disable-save-password-bubble',
                          '--window-size=1500,1000', *urls])
        time.sleep(6)
        from urllib.request import urlopen
        pages = json.load(urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=5))
        title = next((p['title'] for p in pages if p.get('type') == 'page'), '')
        foreground(title or 'Chrome')
        where = 'A Chrome window is already open in front, showing ' + ' and '.join(urls) + '.'
        if task.get('calc'):
            where += ' Windows Calculator is also open behind it.'
        return where
    if task.get('app') == 'calc':
        subprocess.run(['taskkill', '/IM', 'CalculatorApp.exe', '/F'], capture_output=True)
        time.sleep(1)
        subprocess.Popen(['cmd', '/c', 'start', 'calculator:'])
        time.sleep(3)
        foreground('Calculator')
        return 'Windows Calculator is already open in front.'
    raise ValueError('task has no setup')


def run_agent(arm, prompt, run_dir, model, timeout):
    config = run_dir / 'mcp.json'
    config.write_text(json.dumps({'mcpServers': {'wm': {'type': 'stdio', 'command': PY,
                                                        'args': ARMS[arm]}}}), encoding='utf-8')
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('CLAUDE_CODE') and k not in ('CLAUDECODE', 'AI_AGENT')}
    env['QC_CDP_PORT'] = str(PORT)
    env['MCP_TIMEOUT'] = '60000'
    env['PYTHONPATH'] = str(ROOT)
    env['QC_ROUTINES'] = str(OUT / 'routines')
    cmd = [shutil.which('claude'), '-p', prompt, '--model', model, '--mcp-config', str(config),
           '--strict-mcp-config', '--tools', '', '--allowedTools', 'mcp__wm',
           '--permission-mode', 'bypassPermissions', '--output-format', 'stream-json', '--verbose',
           '--no-session-persistence', '--max-turns', '80', '--setting-sources', 'project']
    workdir = Path(tempfile.mkdtemp(prefix='qc-agent-'))
    started = time.perf_counter()
    with open(run_dir / 'stream.jsonl', 'w', encoding='utf-8') as sink:
        try:
            proc = subprocess.run(cmd, stdout=sink, stderr=subprocess.PIPE, text=True, env=env,
                                  timeout=timeout, cwd=str(workdir), encoding='utf-8')
            code = proc.returncode
            err = proc.stderr[-2000:]
        except subprocess.TimeoutExpired:
            code, err = 'timeout', ''
    wall = time.perf_counter() - started
    events = []
    for line in (run_dir / 'stream.jsonl').read_text(encoding='utf-8').splitlines():
        try:
            events.append(json.loads(line))
        except Exception:
            pass
    result = next((e for e in reversed(events) if e.get('type') == 'result'), {})
    tools, tool_ms = [], 0
    pending = {}
    first_assistant = None
    for event in events:
        if event.get('type') == 'assistant':
            first_assistant = first_assistant or event
            for block in event.get('message', {}).get('content', []):
                if block.get('type') == 'tool_use':
                    tools.append(block.get('name', '').replace('mcp__wm__', ''))
    # Runs where Windows MCP paused for physical input are not comparable.
    interrupted = 0
    for event in events:
        if event.get('type') == 'user':
            for block in event.get('message', {}).get('content', []):
                if isinstance(block, dict) and block.get('type') == 'tool_result':
                    content = block.get('content')
                    text = content if isinstance(content, str) else json.dumps(content)
                    if 'USER_CONTROL' in text or 'CONTROL_UNAVAILABLE' in text or '"state":"user"' in text.replace(' ', ''):
                        interrupted += 1
    usage = result.get('usage', {})
    return {
        'exit': code, 'stderr': err, 'wall_s': round(wall, 2),
        'agent_s': round(result.get('duration_ms', 0) / 1000, 2),
        'api_s': round(result.get('duration_api_ms', 0) / 1000, 2),
        'turns': result.get('num_turns'), 'tool_calls': len(tools), 'tools': tools,
        'cost_usd': result.get('total_cost_usd'),
        'input_tokens': (usage.get('input_tokens', 0) + usage.get('cache_read_input_tokens', 0)
                         + usage.get('cache_creation_input_tokens', 0)),
        'output_tokens': usage.get('output_tokens'),
        'answer': result.get('result', ''), 'subtype': result.get('subtype'),
        'interrupted_calls': interrupted,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--arms', default='stock,qc')
    parser.add_argument('--tasks', default=','.join(TASKS))
    parser.add_argument('--reps', type=int, default=1)
    parser.add_argument('--model', default='sonnet')
    parser.add_argument('--timeout', type=int, default=480)
    parser.add_argument('--suffix', default='', help='Extra prompt text (for routine experiments)')
    parser.add_argument('--tag', default='')
    parser.add_argument('--record', action='store_true', help='Record the 1500x1000 browser area')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    log = OUT / 'runs.jsonl'
    for rep in range(args.reps):
        for task_id in args.tasks.split(','):
            # Alternate arm order per rep so neither side always runs first.
            arms = args.arms.split(',')
            if rep % 2:
                arms = arms[::-1]
            for arm in arms:
                task = TASKS[task_id]
                run_dir = OUT / f'{time.strftime("%m%d-%H%M%S")}-{task_id}-{arm}'
                run_dir.mkdir(parents=True)
                where = setup(task, run_dir)
                prompt = PREAMBLE.format(where=where) + task['prompt'] + args.suffix
                row = {'task': task_id, 'arm': arm, 'rep': rep, 'tag': args.tag, 'model': args.model,
                       'started': time.strftime('%Y-%m-%d %H:%M:%S'), 'dir': run_dir.name}
                recorder = None
                if args.record:
                    sys.path.insert(0, str(ROOT))
                    from demos.video import Recorder
                    recorder = Recorder(run_dir / 'screen.mp4', (0, 0, 1500, 1000))
                    recorder.start()
                row.update(run_agent(arm, prompt, run_dir, args.model, args.timeout))
                if recorder:
                    time.sleep(1.5)
                    recorder.stop()
                try:
                    if task.get('needs_profile'):
                        row['check'] = task['check'](row['answer'], task.get('_profile'))
                    else:
                        row['check'] = task['check'](row['answer'])
                except Exception as error:
                    row['check'] = {'passed': False, 'error': f'{type(error).__name__}: {error}'}
                row['passed'] = bool(row['check'].get('passed'))
                (run_dir / 'row.json').write_text(json.dumps(row, indent=1), encoding='utf-8')
                with open(log, 'a', encoding='utf-8') as handle:
                    handle.write(json.dumps(row) + '\n')
                print(f"{task_id:7} {arm:5} pass={row['passed']!s:5} agent={row['agent_s']:6.1f}s "
                      f"turns={row['turns']} tools={row['tool_calls']} cost=${row['cost_usd'] or 0:.3f} "
                      f"int={row['interrupted_calls']} | {row['answer'][:80]!r}", flush=True)
    kill_bench_chrome()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
