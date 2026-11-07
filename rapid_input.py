"""Optional clipboard-free, chunked Unicode input for Windows MCP.

Call ``install()`` before serving tools. Ordinary shortcuts remain upstream;
only Desktop.type is adapted. A successful SendInput call confirms queueing,
not application consumption: callers must still verify the resulting value.
"""
from __future__ import annotations

import ctypes
import functools
import time
from dataclasses import dataclass
from uia_context import check_cancelled


@dataclass(frozen=True)
class KeyEvent:
    vk: int = 0
    scan: int = 0
    flags: int = 0

    def up(self):
        return KeyEvent(self.vk, self.scan, self.flags | 2)


def key_pair(vk):
    down = KeyEvent(vk=vk)
    return [down, down.up()]


def text_chunks(text, chunk_size=32):
    """Yield balanced input batches, never splitting a surrogate pair.

    Keep upstream's literal braces, newline=Enter, tab=Tab and ignored CR.
    Control characters get separate batches as they may change focus.
    """
    if chunk_size < 1 or chunk_size > 256:
        raise ValueError('chunk_size must be between 1 and 256')
    text.encode('utf-16-le')  # Reject malformed surrogates before any input.
    events = []
    count = 0
    for char in text:
        if char == '\r':
            continue
        if char in '\n\t':
            if events:
                yield events
                events, count = [], 0
            yield key_pair(13 if char == '\n' else 9)
            continue
        encoded = char.encode('utf-16-le')
        for offset in range(0, len(encoded), 2):
            down = KeyEvent(scan=int.from_bytes(encoded[offset:offset + 2], 'little'), flags=4)
            events.extend((down, down.up()))
        count += 1
        if count == chunk_size:
            yield events
            events, count = [], 0
    if events:
        yield events


class NativeSender:
    """Use one actual Win32 SendInput call per batch (upstream loops)."""
    def __init__(self, uia):
        self.uia = uia
        self.send_input = ctypes.WinDLL('user32', use_last_error=True).SendInput
        self.send_input.argtypes = [ctypes.c_uint, ctypes.POINTER(uia.INPUT), ctypes.c_int]
        self.send_input.restype = ctypes.c_uint

    def __call__(self, events):
        array = (self.uia.INPUT * len(events))(*[
            self.uia.KeyboardInput(event.vk, event.scan, event.flags)
            for event in events
        ])
        return self.send_input(len(events), array, ctypes.sizeof(self.uia.INPUT))


def send_batch(owner, sender, events):
    """Ledger guards injection and cleans unmatched downs on partial failure.

    No mutation retry: a partial batch raises because replay would duplicate
    text. A native exception has unknown progress, so release every possible
    key in that batch. The ledger retains failed cleanup for watchdog retry.
    """
    check_cancelled()
    owner.checkpoint_current()
    pending = list(events)  # Conservative cleanup if native call raises.

    def release():
        held = {}
        for event in pending:
            identity = (event.vk, event.scan, event.flags & ~2)
            if event.flags & 2:
                held.pop(identity, None)
            else:
                held[identity] = event
        if held:
            # Cleanup may be retried after user takeover. A physical down that
            # reached the app owns its matching up; do not steal that key.
            ups = [event.up() for event in held.values()
                   if not event.vk or not owner.physical_key_down(event.vk)]
            if ups and sender(ups) != len(ups):
                raise OSError('SendInput key-release cleanup was incomplete')

    def inject():
        nonlocal pending
        try:
            inserted = sender(events)
        except BaseException:
            # Unknown progress; all possible downs require release.
            pending = [event for event in events if not event.flags & 2]
            raise
        pending = events[:inserted]
        if inserted:
            owner.record_step_current()
        if inserted != len(events):
            raise OSError(f'SendInput inserted {inserted}/{len(events)} events; input was not retried')

    name = 'quickcontrol:input-batch'
    owner.input_ledger.press(name, inject, release, lambda: False)
    owner.input_ledger.release(name)


def type_text(desktop, loc, text, caret_position='idle', clear=False,
              press_enter=False, *, owner, uia, sender, as_bool, chunk_size=32,
              foreground=None, focus_wait=None, verify_focus=False,
              verification_timeout=0.75, tab_settle=0.03, tab_timeout=0.5):
    """Desktop.type implementation with explicit dependencies for offline tests."""
    clear = as_bool(clear, 'clear')
    press_enter = as_bool(press_enter, 'press_enter')
    # Preflight all content/options before clicking or clearing.
    text.encode('utf-16-le')
    if chunk_size < 1 or chunk_size > 256:
        raise ValueError('chunk_size must be between 1 and 256')
    if focus_wait is not None and not 0.05 <= focus_wait <= 2:
        raise ValueError('focus_wait must be between 0.05 and 2 seconds')
    if verify_focus and '\t' in text:
        raise ValueError('Focused-control verification supports one field; Tab traversal is unsupported')
    if verify_focus and not 0 < verification_timeout <= 5:
        raise ValueError('verification_timeout must be greater than zero and at most 5 seconds')
    check_cancelled()
    owner.checkpoint_current()
    desktop._validate_screen_point(*loc)
    if focus_wait is None:
        uia.Click(*loc)
    else:
        # Click itself retains upstream's 50 ms between mouse down and up.
        # This tunes only the post-click focus settling interval.
        uia.Click(*loc, waitTime=focus_wait)
    owner.record_step_current()
    target_window = foreground() if foreground is not None else None

    def focused_pattern():
        control = uia.GetFocusedControl()
        if control is None:
            raise RuntimeError('No focused control')
        rect = control.BoundingRectangle
        if not (rect.left <= loc[0] < rect.right and rect.top <= loc[1] < rect.bottom):
            raise RuntimeError('Focused control does not contain the requested point')
        pattern = control.GetValuePattern()
        if pattern is None:
            raise RuntimeError('Focused control has no readable value pattern')
        return pattern

    def wait_until(read, accepted, reason):
        deadline = time.monotonic() + verification_timeout
        while True:
            check_cancelled()
            owner.checkpoint_current()
            try:
                value = read()
                if accepted(value):
                    return value
            except Exception:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError(reason)
            time.sleep(0.01)

    if verify_focus:
        # This experimental mode is deliberately strict: do not silently
        # downgrade to a timed delay if the focused field is unreadable.
        initial_value = wait_until(lambda: focused_pattern().Value,
                                   lambda value: isinstance(value, str),
                                   'Focused field could not be verified; no text was entered')
        if initial_value and not clear:
            raise RuntimeError('Verification of a nonempty field requires clear=True; no text was entered')

    def emit(events):
        if foreground is not None and (not target_window or foreground() != target_window):
            raise RuntimeError('Foreground window changed during text entry; input was stopped')
        if verify_focus:
            # Recheck focus before every bounded chunk, without reading a tree.
            focused_pattern()
        send_batch(owner, sender, events)

    if caret_position in ('start', 'end'):
        emit(key_pair(36 if caret_position == 'start' else 35))
    if clear:
        # One atomic batch orders modifier release before Backspace. Keeping
        # everything in the ledger allows cleanup after any partial prefix.
        ctrl = KeyEvent(vk=17)
        emit([ctrl, *key_pair(65), ctrl.up(), *key_pair(8)])
    def focus_identity():
        try:
            rect = uia.GetFocusedControl().BoundingRectangle
            return (rect.left, rect.top, rect.right, rect.bottom)
        except Exception:
            return None

    def await_focus_move(before):
        # Keys sent before the page finishes moving focus can be dropped
        # (seen live on Google Advanced Search), so each field waits.
        if before is None:
            time.sleep(tab_settle)
            return
        deadline = time.monotonic() + tab_timeout
        while True:
            check_cancelled()
            owner.checkpoint_current()
            current = focus_identity()
            if current is not None and current != before:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Focus did not move after Tab; remaining text was not entered')
            time.sleep(0.005)
        time.sleep(tab_settle)

    tab = key_pair(9)
    for events in text_chunks(text, chunk_size):
        before = focus_identity() if events == tab else None
        emit(events)
        if events == tab:
            await_focus_move(before)
    if verify_focus:
        expected = text.replace('\r', '')
        wait_until(lambda: focused_pattern().Value,
                   lambda value: value.replace('\r', '') == expected,
                   'Focused field value did not match; input was not retried')
    if press_enter:
        emit(key_pair(13))
    check_cancelled()
    owner.checkpoint_current()


def install(*, chunk_size=32, focus_wait=None, verify_focus=False,
            verification_timeout=0.75):
    """Opt-in startup adapter; return original method for rollback/testing."""
    from windows_mcp.desktop import service
    if getattr(service.Desktop.type, '_quickcontrol_rapid', False):
        return service.Desktop.type.__wrapped__
    sender = NativeSender(service.uia)
    original = service.Desktop.type

    @functools.wraps(original)
    def adapted(self, loc, text, caret_position='idle', clear=False, press_enter=False):
        return type_text(self, loc, text, caret_position, clear, press_enter,
                         owner=service.get_controller(), uia=service.uia,
                         sender=sender, as_bool=service.as_bool, chunk_size=chunk_size,
                         foreground=service.win32gui.GetForegroundWindow,
                         focus_wait=focus_wait, verify_focus=verify_focus,
                         verification_timeout=verification_timeout)

    adapted._quickcontrol_rapid = True
    service.Desktop.type = adapted
    return original
