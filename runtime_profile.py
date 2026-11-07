"""Adapt Windows MCP at process startup without changing installed package files."""
import ctypes
import functools
import inspect
import time

from control_policy import EscapeTakeover, default_displays


def install():
    from windows_mcp.desktop import control_hooks as hooks
    from windows_mcp.desktop import control_overlay, control_overlay_art
    from windows_mcp.desktop.service import Desktop
    from windows_mcp.tools import snapshot

    if getattr(hooks, '_quickcontrol_installed', False):
        return
    original_key = hooks.physical_key
    original_mouse = hooks.physical_mouse
    original_raw = hooks.raw_input
    original_state = Desktop.get_state
    original_register = snapshot.register
    signature = inspect.signature(original_state)
    if not {'display_indices', 'region'}.issubset(signature.parameters):
        raise RuntimeError('Unsupported Windows MCP get_state API; QuickControl profile not installed')

    def active(owner, now):
        if owner._emergency or owner._state not in ('ai', 'takeover_pending'):
            return False
        if not (owner._suppress or owner._visual_paused):
            return False
        # Keep upstream's one-second hook/watchdog fault release, including
        # during screenshot suspension. A stale hook must never trap input.
        if now > owner._deadline:
            owner._fail_open()
            return False
        return True

    def key(owner, code, wparam, lparam):
        if code < 0:
            return original_key(owner, code, wparam, lparam)
        try:
            data = ctypes.cast(lparam, ctypes.POINTER(hooks._KeyHookData)).contents
            if data.flags & 0x10:
                return original_key(owner, code, wparam, lparam)
            vk = int(data.vkCode)
            down = wparam in (0x100, 0x104)
            now = time.monotonic()
            swallowed = getattr(owner, '_qc_swallowed', None)
            if swallowed is None:
                owner._qc_swallowed = swallowed = set()
                owner._qc_escape = EscapeTakeover()
            if active(owner, now):
                if down:
                    swallowed.add(vk)
                else:
                    swallowed.discard(vk)
                if owner._qc_escape.key(vk, down, now, owner._generation):
                    owner._last_physical_event = now
                    owner._fast_takeover = True
                    owner._suppress = False
                    owner.input_ledger.block_new()
                    owner._queue(('hotkey',))
                return 1
            # Do not leak a release/repeat into the app after swallowing its down.
            if vk in swallowed:
                if not down:
                    swallowed.discard(vk)
                return 1
        except Exception:
            owner._fail_open()
        return original_key(owner, code, wparam, lparam)

    def mouse(owner, code, wparam, lparam):
        if code >= 0:
            try:
                data = ctypes.cast(lparam, ctypes.POINTER(hooks._MouseHookData)).contents
                if not data.flags & 1 and active(owner, time.monotonic()):
                    return 1
            except Exception:
                owner._fail_open()
        return original_mouse(owner, code, wparam, lparam)

    def raw(owner, lparam):
        if active(owner, time.monotonic()):
            return
        return original_raw(owner, lparam)

    @functools.wraps(original_state)
    def get_state(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        selected = bound.arguments.get('display_indices')
        region = bound.arguments.get('region')
        if selected is None and region is None:
            bound.arguments['display_indices'] = default_displays(
                bound.arguments['self'].get_displays(), selected, region)
        return original_state(*bound.args, **bound.kwargs)

    def register(mcp, **kwargs):
        class Registrar:
            def tool(self, **metadata):
                if metadata.get('name') in ('Screenshot', 'Snapshot'):
                    description = metadata.get('description', '').replace(
                        'omit it to keep the default full-desktop behavior',
                        'omit it to capture the primary display')
                    metadata['description'] = description + (
                        ' QuickControl defaults to the primary display. '
                        'Use display=[1] for another display or display=[0,1] for both. '
                        'Explicit region coordinates override the default display.')
                return mcp.tool(**metadata)
        return original_register(Registrar(), **kwargs)

    hooks.physical_key = key
    hooks.physical_mouse = mouse
    hooks.raw_input = raw
    Desktop.get_state = get_state
    snapshot.register = register
    # Retain the blue edge/cursor indicator and its health acknowledgements.
    # Only remove the old shortcut panel; both modules hold a function reference.
    control_overlay._notice_bitmap = lambda width: None
    control_overlay_art._notice_bitmap = control_overlay._notice_bitmap
    hooks._quickcontrol_installed = True
