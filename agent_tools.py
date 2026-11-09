"""QuickControl agent tools: text observation, whole-plan actions, menu surveys, routines.

The point is fewer model turns. Observe returns a numbered text list instead of
a screenshot. Act runs a whole plan of steps, checks each one, and returns the
new observation, so one model turn can cover a page or several pages.
Chrome is driven over its local DevTools port; native windows through UIA.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import qc_browser as cdp

ROUTINES = Path(os.environ.get('QC_ROUTINES', Path.home() / '.quickcontrol' / 'routines'))
CHROME = os.environ.get('QC_CHROME', r'C:\Program Files\Google\Chrome\Application\chrome.exe')
MAX_STEPS = 40
BROWSER = cdp.Browser()
_desktop_cache = {'hwnd': None, 'elements': []}

STEP_HELP = (
    'Steps (each a JSON object): '
    '{"click": T} | {"fill": T, "text": "..."} (replaces the field value) | '
    '{"select": T, "option": "label"} (native select/combobox) | {"check": T, "on": true} | '
    '{"press": "Enter"} (keys: Enter, Tab, Escape, ArrowDown, ctrl+a, ...) | '
    '{"type": "text"} (into the focused element) | {"goto": "https://..."} (browser) | '
    '{"window": "title"} (desktop: switch to window) | '
    '{"wait": {"text": "..."} or {"url": "..."} or {"window": "..."}, "timeout": 10} | '
    '{"expect": {"text": "..."} or {"url": "..."}} (stop the plan if false) | '
    '{"scroll": "down"} | {"find": "words"} (returns matching page text). '
    'T is an element number from the latest Observe, or {"text": "visible label", "role": "button", "near": "card/row text", "nth": 0} '
    'which is re-resolved on the current page, so plans can span page loads.'
)


def _subst(value, variables):
    if isinstance(value, str):
        return re.sub(r'\{\{(\w+)\}\}', lambda m: str(variables.get(m.group(1), m.group(0))), value)
    if isinstance(value, list):
        return [_subst(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: _subst(v, variables) for k, v in value.items()}
    return value


def routine_names():
    if not ROUTINES.exists():
        return []
    out = []
    for path in sorted(ROUTINES.glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            out.append(f"{path.stem}({', '.join(data.get('vars', []))}): {data.get('description', '')}")
        except Exception:
            continue
    return out


def _foreground_kind():
    import qc_desktop as desk
    import ctypes
    hwnd = desk.foreground_hwnd()
    buffer = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buffer, 256)
    return ('chrome' if buffer.value == 'Chrome_WidgetWin_1' else 'native'), hwnd


# ---------------------------------------------------------------- browser

def browser_observe(all_elements=False, max_text=1500, find=None):
    page = BROWSER.connect()
    data, ms = cdp.observe(page, all_elements, max_text)
    text = cdp.format_observation(data, ms)
    if find:
        hits = page.lib(f'find({json.dumps(find)}, 6)')
        text += f'\nFind "{find}": ' + (' || '.join(hits) if hits else 'no matches')
    return text


def _target(step, key):
    target = step[key]
    if isinstance(target, str) and not target.isdigit():
        target = {'text': target}
    elif isinstance(target, str):
        target = int(target)
    if isinstance(target, dict):
        target = {k: target[k] for k in ('text', 'name', 'role', 'nth', 'near') if k in target}
    return target


def browser_step(page, step, record):
    """Run one step; return a short result string. Raise to stop the plan."""
    js = json.dumps
    if 'click' in step:
        target = _target(step, 'click')
        before = cdp.state_token(page)
        point = page.lib(f'point({js(target)})')
        if not point['ok']:
            raise RuntimeError(f'Element "{point["name"]}" is covered by {point["blocker"]}; not clicked')
        cdp.mouse_click(page, point['x'], point['y'], int(step.get('clicks', 1)))
        record({'click': {'text': point['name'], 'role': point['role']}} if point['name'] else step)
        nav = cdp.settle(page, before)
        return f'clicked {point["role"]} "{point["name"]}"' + (' -> page loaded' if nav else '')
    if 'fill' in step:
        target = _target(step, 'fill')
        text = str(step.get('text', ''))
        info = page.lib(f'point({js(target)})')
        if not page.lib(f'focusFor({js(target)})'):
            raise RuntimeError(f'Could not focus "{info["name"]}"')
        cdp.press(page, 'ctrl+a')
        cdp.press(page, 'Delete')
        if text:
            page.send('Input.insertText', {'text': text})
        time.sleep(0.02)
        actual = page.lib(f'valueOf({js(target)})')
        record({'fill': {'text': info['name'], 'role': info['role']}, 'text': step.get('_template', text)})
        if actual is not None and str(actual) != text:
            return f'filled "{info["name"]}" but the field shows "{actual}"'
        return f'filled "{info["name"]}"'
    if 'select' in step:
        target = _target(step, 'select')
        info = page.lib(f'point({js(target)})')
        chosen = page.lib(f'choose({js(target)}, {js(step.get("option", ""))})')
        record({'select': {'text': info['name'], 'role': 'select'}, 'option': step.get('_option_template', chosen)})
        time.sleep(0.05)
        return f'selected "{chosen}" in "{info["name"]}"'
    if 'check' in step:
        target = _target(step, 'check')
        want = bool(step.get('on', True))
        state = page.lib(f'info(window.__qcLib.resolve({js(target)}))')
        record({'check': {'text': state['name'], 'role': state['role']}, 'on': want})
        if bool(state.get('checked')) == want:
            return f'"{state["name"]}" already {"checked" if want else "unchecked"}'
        point = page.lib(f'point({js(target)})')
        cdp.mouse_click(page, point['x'], point['y'])
        time.sleep(0.05)
        return f'{"checked" if want else "unchecked"} "{state["name"]}"'
    if 'press' in step:
        before = cdp.state_token(page)
        cdp.press(page, step['press'])
        record(step)
        nav = cdp.settle(page, before) if str(step['press']).lower() == 'enter' else False
        return f'pressed {step["press"]}' + (' -> page loaded' if nav else '')
    if 'type' in step:
        page.send('Input.insertText', {'text': str(step['type'])})
        record(step)
        return 'typed text'
    if 'goto' in step:
        url = str(step['goto'])
        if not url.startswith(('http://', 'https://')):
            raise ValueError('goto needs an http(s) URL')
        before = cdp.state_token(page)
        page.send('Page.navigate', {'url': url})
        cdp.settle(page, before, quiet=2.0)
        record(step)
        return f'opened {url}'
    if 'wait' in step or 'expect' in step:
        key = 'wait' if 'wait' in step else 'expect'
        condition = step[key] if isinstance(step[key], dict) else {'text': str(step[key])}
        timeout = float(step.get('timeout', 10 if key == 'wait' else 3))
        ok = cdp.wait_for(page, condition, min(timeout, 30))
        record(step)
        if not ok:
            raise RuntimeError(f'{key} failed: {condition} not true after {timeout:g} s')
        return f'{key} ok: {condition}'
    if 'scroll' in step:
        where = step['scroll']
        if where in ('down', 'up'):
            page.eval(f'scrollBy(0, {"" if where == "down" else "-"}innerHeight*0.8)')
        else:
            page.lib(f'resolve({js(_target(step, "scroll"))}).scrollIntoView({{block:"center"}})')
        time.sleep(0.1)
        record(step)
        return f'scrolled {where}'
    if 'find' in step:
        hits = page.lib(f'find({js(str(step["find"]))}, 6)')
        return f'find "{step["find"]}": ' + (' || '.join(hits) if hits else 'no matches')
    raise ValueError(f'Unknown step {step}')


def browser_survey(targets, images):
    """Open each custom dropdown, list what appears, close it. One call, all menus."""
    page = BROWSER.connect()
    results, pictures = [], []
    for raw in targets:
        target = raw if isinstance(raw, int) else {'text': str(raw)} if isinstance(raw, str) else raw
        try:
            info = page.lib(f'point({json.dumps(target)})')
            before = {item['s'] for item in page.lib('popupItems().map(i => ({s: i.s}))')}
            cdp.mouse_click(page, info['x'], info['y'])
            items, deadline = [], time.monotonic() + 1.2
            while time.monotonic() < deadline:
                time.sleep(0.08)
                now = page.lib('popupItems().map(i => ({s: i.s, r: [i.r.left, i.r.top, i.r.width, i.r.height]}))')
                items = [i for i in now if i['s'] not in before]
                if items:
                    time.sleep(0.12)
                    now = page.lib('popupItems().map(i => ({s: i.s, r: [i.r.left, i.r.top, i.r.width, i.r.height]}))')
                    items = [i for i in now if i['s'] not in before]
                    break
            names = list(dict.fromkeys(i['s'] for i in items))
            if images and items:
                left = min(i['r'][0] for i in items)
                top = min(i['r'][1] for i in items)
                right = max(i['r'][0] + i['r'][2] for i in items)
                bottom = max(i['r'][1] + i['r'][3] for i in items)
                pictures.append((info['name'], cdp.capture(page, (left, top, right - left, bottom - top))))
            cdp.press(page, 'Escape')
            time.sleep(0.08)
            still = [i for i in page.lib('popupItems().map(i => ({s: i.s}))') if i['s'] not in before]
            if still:
                cdp.press(page, 'Escape')
            results.append(f'"{info["name"]}": ' + (' | '.join(names[:60]) if names else 'nothing appeared'))
        except Exception as error:
            results.append(f'{raw}: error {error}')
    return results, pictures


# ---------------------------------------------------------------- desktop

def desktop_observe(window=None, max_text=1500):
    import qc_desktop as desk
    hwnd = desk.find_window(window) if window else desk.foreground_hwnd()
    if not hwnd:
        raise LookupError(f'No window titled like {window!r}')
    started = time.perf_counter()
    elements, texts = desk.scan(hwnd)
    ms = (time.perf_counter() - started) * 1000
    _desktop_cache.update(hwnd=hwnd, elements=elements)
    return desk.describe(elements, texts, desk.window_title(hwnd), max_text, ms)


def _desktop_resolve(target, rescan=True):
    import qc_desktop as desk
    if isinstance(target, int):
        elements = _desktop_cache['elements']
        if not 1 <= target <= len(elements):
            raise LookupError(f'No element [{target}] in the latest observation')
        return elements[target - 1]
    if isinstance(target, str):
        target = {'name': target}
    hwnd = desk.foreground_hwnd()
    if rescan and _desktop_cache['hwnd'] == hwnd and _desktop_cache['elements']:
        try:
            return desk.match(_desktop_cache['elements'], target)
        except LookupError:
            pass
    elements, _ = desk.scan(hwnd)
    _desktop_cache.update(hwnd=hwnd, elements=elements)
    return desk.match(elements, target)


def desktop_step(step, record, get_desktop):
    import qc_desktop as desk
    from windows_mcp.desktop import service
    if 'window' in step and len(step) <= 2 and 'wait' not in step:
        title = str(step['window'])
        hwnd = desk.find_window(title)
        if not hwnd:
            raise LookupError(f'No window titled like {title!r}')
        import ctypes
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        desk.wait_window(desk.window_title(hwnd), 3)
        record(step)
        return f'switched to "{desk.window_title(hwnd)}"'
    if 'click' in step:
        element = _desktop_resolve(_target(step, 'click'))
        record({'click': {'name': element.name, 'role': element.role}})
        if element.role in ('button', 'menuitem', 'splitbutton', 'link', 'listitem') and desk.invoke(element):
            time.sleep(0.03)
            return f'invoked {element.role} "{element.name}"'
        x, y = element.center
        service.uia.Click(x, y, waitTime=0.05)
        time.sleep(0.05)
        return f'clicked {element.role} "{element.name}"'
    if 'fill' in step:
        element = _desktop_resolve(_target(step, 'fill'))
        text = str(step.get('text', ''))
        record({'fill': {'name': element.name, 'role': element.role}, 'text': step.get('_template', text)})
        if desk.set_value(element, text):
            actual = desk.current_value(element)
            if actual is None or actual.replace('\r', '') == text.replace('\r', ''):
                return f'set "{element.name}"'
        desk.focus(element)
        get_desktop().shortcut('ctrl+a')
        _native_type(text)
        return f'typed into "{element.name}"'
    if 'type' in step:
        _native_type(str(step['type']))
        record(step)
        return 'typed text'
    if 'press' in step:
        get_desktop().shortcut(str(step['press']))
        record(step)
        time.sleep(0.05)
        return f'pressed {step["press"]}'
    if 'select' in step:
        element = _desktop_resolve(_target(step, 'select'))
        option = str(step.get('option', ''))
        record({'select': {'name': element.name, 'role': element.role}, 'option': option})
        if desk.set_value(element, option):
            return f'set "{element.name}" to "{option}"'
        desk.invoke(element) or service.uia.Click(*element.center, waitTime=0.05)
        time.sleep(0.25)
        item = _desktop_resolve({'name': option, 'role': 'listitem'})
        desk.invoke(item) or service.uia.Click(*item.center, waitTime=0.05)
        return f'chose "{option}"'
    if 'check' in step:
        element = _desktop_resolve(_target(step, 'check'))
        want = bool(step.get('on', True))
        record({'check': {'name': element.name, 'role': element.role}, 'on': want})
        if (desk.toggle_state(element) == 1) == want:
            return f'"{element.name}" already set'
        service.uia.Click(*element.center, waitTime=0.05)
        return f'toggled "{element.name}"'
    if 'wait' in step or 'expect' in step:
        key = 'wait' if 'wait' in step else 'expect'
        condition = step[key] if isinstance(step[key], dict) else {'text': str(step[key])}
        timeout = min(float(step.get('timeout', 10 if key == 'wait' else 2)), 30)
        record(step)
        deadline = time.monotonic() + timeout
        while True:
            hwnd = desk.foreground_hwnd()
            title = desk.window_title(hwnd)
            ok = True
            if condition.get('window'):
                ok = condition['window'].casefold() in title.casefold()
            if ok and condition.get('text'):
                elements, texts = desk.scan(hwnd)
                blob = ' '.join(texts + [e.name for e in elements] + [e.value for e in elements]).casefold()
                ok = condition['text'].casefold() in blob
            if ok:
                return f'{key} ok: {condition}'
            if time.monotonic() >= deadline:
                raise RuntimeError(f'{key} failed: {condition} not true after {timeout:g} s')
            time.sleep(0.05)
    raise ValueError(f'Step not supported on desktop windows: {step}')


def _native_type(text):
    from windows_mcp.desktop import service
    from rapid_input import NativeSender, send_batch, text_chunks
    owner = service.get_controller()
    sender = NativeSender(service.uia)
    for events in text_chunks(text, 32):
        send_batch(owner, sender, events)


# ---------------------------------------------------------------- plans

def prepare(steps, variables):
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f'steps must be a list of 1 to {MAX_STEPS} objects')
    prepared = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not step:
            raise ValueError(f'step {index} must be a JSON object')
        concrete = _subst(step, variables)
        if 'text' in step and isinstance(step.get('text'), str):
            concrete['_template'] = step['text']
        if 'option' in step:
            concrete['_option_template'] = step['option']
        prepared.append(concrete)
    return prepared


def run_plan(steps, *, kind, checkpoint, get_desktop, save_as=None, description='', variables=None):
    variables = variables or {}
    recorded, log = [], []
    started = time.perf_counter()
    page = BROWSER.connect() if kind == 'browser' else None
    failed = None
    for index, step in enumerate(steps):
        checkpoint()
        step_started = time.perf_counter()
        try:
            if kind == 'browser':
                outcome = browser_step(page, step, recorded.append)
            else:
                outcome = desktop_step(step, recorded.append, get_desktop)
            log.append(f'{index + 1}. {outcome} ({(time.perf_counter() - step_started) * 1000:.0f} ms)')
        except Exception as error:
            failed = index
            log.append(f'{index + 1}. FAILED: {error}. Remaining {len(steps) - index - 1} steps not run; nothing was retried.')
            break
    total = (time.perf_counter() - started) * 1000
    if save_as and failed is None:
        ROUTINES.mkdir(parents=True, exist_ok=True)
        clean = [{k: v for k, v in s.items() if not k.startswith('_')} for s in recorded]
        names = sorted(set(re.findall(r'\{\{(\w+)\}\}', json.dumps(clean))))
        (ROUTINES / f'{re.sub(r"[^A-Za-z0-9_-]", "-", save_as)}.json').write_text(json.dumps({
            'description': description, 'kind': kind, 'vars': names, 'steps': clean,
            'saved': time.strftime('%Y-%m-%d %H:%M')}, indent=1), encoding='utf-8')
        log.append(f'Saved routine "{save_as}" ({len(clean)} steps, vars: {", ".join(names) or "none"}).')
    return failed, log, total


def register(mcp, *, get_desktop, get_analytics):
    from mcp.types import ImageContent, TextContent, ToolAnnotations
    from windows_mcp.desktop.control import get_controller
    from windows_mcp.infrastructure import thread_affinity

    def uia(fn, *args, **kwargs):
        return thread_affinity.run_uia_affine(fn, *args, **kwargs)

    async def pick_kind(app):
        if app in ('browser', 'desktop'):
            return app
        kind, _ = await uia(_foreground_kind)
        if kind == 'chrome' and await asyncio.to_thread(BROWSER.available):
            return 'browser'
        return 'desktop'

    @mcp.tool(name='Observe', description=(
        'QuickControl: FAST text view of the active app (use this instead of Screenshot/Snapshot). '
        'Lists interactive elements as [n] role "label" value/options/checked, plus visible text. '
        'Native <select> dropdowns list all their options inline, so no need to open them. '
        'app="auto" uses the browser view when Chrome is in front, else the foreground window; '
        'window="title" observes another desktop window. find="words" also returns page text '
        'around each match (good for reading facts). all=true includes off-screen elements. '
        'Also lists saved routines.'),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=False))
    async def observe_tool(app: str = 'auto', window: str | None = None, find: str | None = None,
                           all: bool = False, max_text: int = 1500) -> str:
        kind = 'desktop' if window else await pick_kind(app)
        if kind == 'browser':
            text = await asyncio.to_thread(browser_observe, all, max_text, find)
        else:
            text = await uia(desktop_observe, window, max_text)
        routines = routine_names()
        if routines:
            text += '\nSaved routines (run with Act(routine=...)): ' + '; '.join(routines)
        return text

    @mcp.tool(name='Act', description=(
        'QuickControl: run a WHOLE PLAN of steps in one call, then get the new Observe view back. '
        'Plan the full task from one observation; each step is checked and the plan stops at the '
        'first failure, reporting what happened. Prefer {"text": label} targets for elements on '
        'later pages, since numbers only refer to the latest observation. ' + STEP_HELP +
        ' Optional: save_as="name" stores a successful plan as a reusable routine, with {{var}} '
        'placeholders allowed in text/option values; routine="name" + vars={...} replays one. '
        'observe=false skips the returned view.'),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                    idempotentHint=False, openWorldHint=True))
    async def act_tool(steps: list[dict] | None = None, app: str = 'auto', observe: bool = True,
                       save_as: str | None = None, description: str = '',
                       routine: str | None = None, vars: dict | None = None) -> str:
        variables = dict(vars or {})
        if routine:
            path = ROUTINES / f'{routine}.json'
            if not path.exists():
                raise ValueError(f'No routine {routine!r}. Saved: {routine_names()}')
            saved = json.loads(path.read_text(encoding='utf-8'))
            missing = [v for v in saved.get('vars', []) if v not in variables]
            if missing:
                raise ValueError(f'Routine {routine!r} needs vars: {missing}')
            steps, app = saved['steps'], saved.get('kind', app)
        if not steps:
            raise ValueError('Provide steps or routine')
        prepared = prepare(steps, variables)
        kind = await pick_kind(app)
        checkpoint = get_controller().checkpoint_current
        if kind == 'browser':
            failed, log, total = await asyncio.to_thread(
                run_plan, prepared, kind=kind, checkpoint=checkpoint, get_desktop=get_desktop,
                save_as=save_as, description=description, variables=variables)
        else:
            failed, log, total = await uia(
                run_plan, prepared, kind=kind, checkpoint=checkpoint, get_desktop=get_desktop,
                save_as=save_as, description=description, variables=variables)
        status = 'all steps done' if failed is None else f'stopped at step {failed + 1}'
        text = f'Act ({kind}): {status} in {total:.0f} ms\n' + '\n'.join(log)
        if observe or failed is not None:
            if kind == 'browser':
                view = await asyncio.to_thread(browser_observe, False, 1200, None)
            else:
                view = await uia(desktop_observe, None, 1200)
            text += '\n--- now ---\n' + view
        return text

    @mcp.tool(name='SurveyMenus', description=(
        'QuickControl: open several CUSTOM dropdowns/menus in one call and list what each one '
        'offers, closing each again (Escape). targets = element numbers or labels from Observe. '
        'Native <select> options already appear in Observe, so use this only for custom '
        'dropdowns (role combobox / menu closed). images=true also returns a cropped picture of '
        'each open menu. Does not choose anything.'),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                    idempotentHint=False, openWorldHint=False))
    async def survey_tool(targets: list, images: bool = False):
        get_controller().checkpoint_current()
        results, pictures = await asyncio.to_thread(browser_survey, targets, images)
        content = [TextContent(type='text', text='\n'.join(results))]
        import base64
        for name, data in pictures:
            content.append(TextContent(type='text', text=f'Menu "{name}":'))
            content.append(ImageContent(type='image', data=base64.b64encode(data).decode(), mimeType='image/jpeg'))
        return content

    @mcp.tool(name='OpenBrowser', description=(
        'QuickControl: open a URL in a Chrome window that Observe/Act can drive quickly '
        '(dedicated profile with a local debugging port). If that Chrome is already running, '
        'navigates its current tab instead.'),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                    idempotentHint=False, openWorldHint=True))
    async def open_browser(url: str) -> str:
        if not url.startswith(('http://', 'https://')):
            raise ValueError('url must start with http:// or https://')
        if await asyncio.to_thread(BROWSER.available):
            page = await asyncio.to_thread(BROWSER.connect)
            before = await asyncio.to_thread(cdp.state_token, page)
            await asyncio.to_thread(page.send, 'Page.navigate', {'url': url})
            await asyncio.to_thread(cdp.settle, page, before, 2.0)
        else:
            port = int(os.environ.get('QC_CDP_PORT', '9333'))
            profile = Path.home() / '.quickcontrol' / 'chrome-profile'
            prefs = profile / 'Default' / 'Preferences'
            if not prefs.exists():
                # Password bubbles and breach warnings block page input until dismissed.
                prefs.parent.mkdir(parents=True, exist_ok=True)
                prefs.write_text(json.dumps({'credentials_enable_service': False, 'profile': {
                    'password_manager_enabled': False, 'password_manager_leak_detection': False}}),
                    encoding='utf-8')
            subprocess.Popen([CHROME, f'--user-data-dir={profile}', f'--remote-debugging-port={port}',
                              f'--remote-allow-origins=http://localhost:{port}', '--no-first-run',
                              '--new-window', url])
            deadline = time.monotonic() + 15
            while not await asyncio.to_thread(BROWSER.available):
                if time.monotonic() > deadline:
                    raise TimeoutError('Chrome did not expose its debugging port')
                await asyncio.sleep(0.2)
            page = await asyncio.to_thread(BROWSER.connect)
            await asyncio.to_thread(cdp.wait_for, page, {'url': url.split('#')[0][:40]}, 10)
        return await asyncio.to_thread(browser_observe, False, 1500, None)


def install():
    from windows_mcp import tools
    modules = getattr(tools, '_MODULES', None)
    if not isinstance(modules, list):
        raise RuntimeError('Unsupported Windows MCP registration API')
    module = sys.modules[__name__]
    if module not in modules:
        modules.append(module)
