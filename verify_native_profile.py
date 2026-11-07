"""Windows-only smoke check using real MCP types, without installing OS hooks."""
import ctypes
import time

from runtime_profile import install


def main():
    install()
    from windows_mcp.desktop.control import ControlCoordinator, ControlBlocked
    from windows_mcp.desktop import control_hooks, control_overlay
    owner = ControlCoordinator()
    with owner._lock:
        owner._set_locked('ai')
    owner._deadline = time.monotonic() + 10
    token = owner._generation
    def key(down):
        data = control_hooks._KeyHookData()
        data.vkCode = 27
        return owner._physical_key(0, 0x100 if down else 0x101, ctypes.addressof(data))
    data = control_hooks._MouseHookData()
    data.pt.x, data.pt.y = 10, 10
    assert owner._physical_mouse(0, 0x200, ctypes.addressof(data)) == 1
    owner._raw_input(0)
    assert not owner._fast_pending and owner._events.empty()
    owner.checkpoint(token)
    assert key(True) == 1
    assert key(True) == 1  # Held-key repeat.
    assert owner._events.empty()
    assert key(False) == 1
    assert key(True) == 1
    event = owner._events.get_nowait()
    assert event == ('hotkey',)
    try:
        owner.checkpoint(token)
    except ControlBlocked:
        pass
    else:
        raise AssertionError('AI input was not stopped immediately')
    owner._handle(event)
    assert owner.status()['state'] == 'user'
    assert not owner._suppress
    assert not owner.input_ledger.pending()
    assert key(False) == 1
    assert control_overlay._notice_bitmap(1920) is None
    print('PASS: real hook structures, mouse/raw filtering, Escape handoff, checkpoint cancellation, hold cleanup, no banner')


if __name__ == '__main__':
    main()
