"""Fast text observation and element actions for native windows through UIA.

One FindAllBuildCache call fetches every property we print in a single
cross-process round trip, instead of one COM call per property per node.
Everything here must run on the UIA worker thread.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

P_NAME, P_TYPE, P_RECT, P_ENABLED, P_AID = 30005, 30003, 30001, 30010, 30011
P_OFFSCREEN, P_VALUE, P_TOGGLE, P_EXPAND, P_FOCUSABLE = 30022, 30045, 30086, 30070, 30009
P_CLASS, P_READONLY = 30012, 30046
PAT_INVOKE, PAT_VALUE, PAT_TOGGLE, PAT_EXPAND, PAT_SELITEM = 10000, 10002, 10015, 10005, 10010
SCOPE_DESCENDANTS = 4

TYPES = {
    50000: 'button', 50002: 'checkbox', 50003: 'combobox', 50004: 'edit', 50005: 'link',
    50007: 'listitem', 50008: 'list', 50009: 'menu', 50011: 'menuitem', 50013: 'radio',
    50015: 'slider', 50018: 'tab', 50019: 'tabitem', 50020: 'text', 50023: 'treeitem',
    50024: 'tree', 50030: 'document', 50031: 'splitbutton', 50032: 'window', 50033: 'pane',
    50036: 'dataitem', 50025: 'custom', 50026: 'group', 50021: 'toolbar', 50037: 'titlebar',
    50010: 'menubar', 50017: 'statusbar', 50029: 'header', 50034: 'headeritem', 50035: 'table',
    50028: 'datagrid', 50016: 'spinner', 50001: 'calendar', 50006: 'image', 50012: 'progressbar',
    50014: 'scrollbar', 50022: 'tooltip', 50027: 'thumb', 50038: 'separator', 50039: 'semanticzoom',
    50040: 'appbar',
}
ACTIONABLE = {'button', 'checkbox', 'combobox', 'edit', 'link', 'listitem', 'menuitem', 'radio',
              'slider', 'tabitem', 'treeitem', 'document', 'splitbutton', 'dataitem', 'spinner'}
TEXTUAL = {'text', 'headeritem', 'statusbar'}

user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


def _automation():
    from windows_mcp.uia.core import _AutomationClient
    return _AutomationClient.instance().IUIAutomation


def foreground_hwnd():
    return user32.GetForegroundWindow()


def window_title(hwnd):
    buffer = ctypes.create_unicode_buffer(1024)
    user32.GetWindowTextW(hwnd, buffer, 1024)
    return buffer.value


def find_window(title):
    """Top-level visible window whose title contains `title` (case-insensitive)."""
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    @callback
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd) and title.casefold() in window_title(hwnd).casefold():
            found.append(hwnd)
        return True
    user32.EnumWindows(visit, 0)
    return found[0] if found else None


class Element:
    __slots__ = ('raw', 'role', 'name', 'value', 'rect', 'enabled', 'state', 'aid')

    def __init__(self, raw, role, name, value, rect, enabled, state, aid):
        self.raw, self.role, self.name, self.value = raw, role, name, value
        self.rect, self.enabled, self.state, self.aid = rect, enabled, state, aid

    @property
    def center(self):
        left, top, right, bottom = self.rect
        return (left + right) // 2, (top + bottom) // 2


def _rect(value):
    try:
        left, top, width, height = (int(v) for v in value)
        return left, top, left + width, top + height
    except Exception:
        return 0, 0, 0, 0


def scan(hwnd, limit=250):
    """Return (elements, texts) for one top-level window, from one cached call."""
    automation = _automation()
    root = automation.ElementFromHandle(hwnd)
    request = automation.CreateCacheRequest()
    for prop in (P_NAME, P_TYPE, P_RECT, P_ENABLED, P_AID, P_OFFSCREEN, P_VALUE,
                 P_TOGGLE, P_EXPAND, P_READONLY):
        request.AddProperty(prop)
    for pattern in (PAT_INVOKE, PAT_VALUE, PAT_TOGGLE, PAT_EXPAND, PAT_SELITEM):
        request.AddPattern(pattern)
    condition = automation.ControlViewCondition
    array = root.FindAllBuildCache(SCOPE_DESCENDANTS, condition, request)
    elements, texts = [], []
    for index in range(array.Length if array else 0):
        raw = array.GetElement(index)
        try:
            if raw.GetCachedPropertyValue(P_OFFSCREEN):
                continue
            role = TYPES.get(raw.GetCachedPropertyValue(P_TYPE), 'other')
            name = (raw.GetCachedPropertyValue(P_NAME) or '').strip()
            rect = _rect(raw.GetCachedPropertyValue(P_RECT))
            if rect[2] - rect[0] < 1 or rect[3] - rect[1] < 1:
                continue
            if role in TEXTUAL:
                if name:
                    texts.append(name)
                continue
            if role not in ACTIONABLE:
                continue
            value = raw.GetCachedPropertyValue(P_VALUE)
            value = value if isinstance(value, str) else ''
            toggle = raw.GetCachedPropertyValue(P_TOGGLE)
            expand = raw.GetCachedPropertyValue(P_EXPAND)
            state = []
            if role in ('checkbox', 'radio') and isinstance(toggle, int):
                state.append('checked' if toggle == 1 else 'unchecked')
            if role == 'combobox' and isinstance(expand, int):
                state.append('expanded' if expand == 1 else 'collapsed')
            elements.append(Element(raw, role, name, value, rect,
                                    bool(raw.GetCachedPropertyValue(P_ENABLED)), state,
                                    raw.GetCachedPropertyValue(P_AID) or ''))
        except Exception:
            continue
        if len(elements) >= limit:
            break
    return elements, texts


def describe(elements, texts, title, max_text=1500, elapsed=None):
    lines = [f'Window: {title}' + (f'  (observed in {elapsed:.0f} ms)' if elapsed is not None else '')]
    for index, element in enumerate(elements, 1):
        name = element.name[:80]
        line = f'[{index}] {element.role} "{name}"'
        if element.value and element.value != name:
            value = element.value.replace('\r', '').replace('\n', '\\n')
            line += f' value="{value[:120]}' + ('..."' if len(value) > 120 else '"')
        if element.state:
            line += ' ' + ' '.join(element.state)
        if not element.enabled:
            line += ' disabled'
        lines.append(line)
    if texts and max_text:
        joined, seen = [], set()
        for text in texts:
            if text not in seen:
                seen.add(text)
                joined.append(text)
        body = ' | '.join(joined)
        lines.append('Text: ' + body[:max_text] + ('...' if len(body) > max_text else ''))
    return '\n'.join(lines)


def match(elements, target):
    """Resolve {"name": "..", "role": "..", "nth": 0} against scanned elements."""
    wanted = str(target.get('name', target.get('text', ''))).casefold().strip()
    role = target.get('role')
    nth = int(target.get('nth', 0))
    scored = []
    for element in elements:
        if role and element.role != role:
            continue
        name = element.name.casefold()
        aid = element.aid.casefold()
        if not wanted:
            score = 1
        elif name == wanted or aid == wanted:
            score = 3
        elif name.startswith(wanted):
            score = 2
        elif wanted in name:
            score = 1
        else:
            continue
        scored.append((score, element))
    if not scored:
        raise LookupError(f'No element matches {target}')
    best = max(score for score, _ in scored)
    candidates = [element for score, element in scored if score == best]
    if nth >= len(candidates):
        raise LookupError(f'Only {len(candidates)} elements match {target}')
    return candidates[nth]


def pattern(raw, pattern_id, interface):
    from windows_mcp.uia.core import _AutomationClient
    core = _AutomationClient.instance().UIAutomationCore
    unknown = raw.GetCurrentPattern(pattern_id)
    if not unknown:
        return None
    return unknown.QueryInterface(getattr(core, interface))


def set_value(element, text):
    """Instant fill through ValuePattern; returns False when unsupported."""
    try:
        value = pattern(element.raw, PAT_VALUE, 'IUIAutomationValuePattern')
        if value is None or value.CurrentIsReadOnly:
            return False
        value.SetValue(text)
        return True
    except Exception:
        return False


def current_value(element):
    try:
        value = pattern(element.raw, PAT_VALUE, 'IUIAutomationValuePattern')
        return value.CurrentValue if value is not None else None
    except Exception:
        return None


def invoke(element):
    try:
        invoke_pattern = pattern(element.raw, PAT_INVOKE, 'IUIAutomationInvokePattern')
        if invoke_pattern is None:
            return False
        invoke_pattern.Invoke()
        return True
    except Exception:
        return False


def focus(element):
    try:
        element.raw.SetFocus()
        return True
    except Exception:
        return False


def toggle_state(element):
    try:
        return element.raw.GetCurrentPropertyValue(P_TOGGLE)
    except Exception:
        return None


def wait_window(title, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = foreground_hwnd()
        if title.casefold() in window_title(hwnd).casefold():
            return hwnd
        time.sleep(0.03)
    raise TimeoutError(f'Window {title!r} did not become active within {timeout:g} s')


def live_rect(element):
    """Current screen rectangle, read now; None when the element is gone."""
    try:
        return _rect(element.raw.GetCurrentPropertyValue(P_RECT))
    except Exception:
        return None


def alive(element):
    """The element still exists with the same name (cached COM refs outlive UI)."""
    try:
        return (element.raw.GetCurrentPropertyValue(P_NAME) or '').strip() == element.name
    except Exception:
        return False


def list_windows(limit=12):
    """Titles of visible top-level app windows, front to back."""
    import ctypes
    from ctypes import wintypes
    GW_OWNER, WS_EX_TOOLWINDOW, GWL_EXSTYLE = 4, 0x80, -20
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowLongW.restype = ctypes.c_long
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    titles = []

    @callback
    def visit(hwnd, _):
        if (user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, GW_OWNER)
                and not user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW):
            title = window_title(hwnd).strip()
            if title and title not in ('Program Manager', 'Windows Input Experience') and title not in titles:
                titles.append(title)
        return len(titles) < limit
    user32.EnumWindows(visit, 0)
    return titles
