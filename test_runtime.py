"""Exercise hook adapters without installing hooks or touching a desktop."""
import ctypes
import sys
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from runtime_profile import install


class KeyData(ctypes.Structure):
    _fields_ = [('vkCode', ctypes.c_uint32), ('flags', ctypes.c_uint32)]


class MouseData(ctypes.Structure):
    _fields_ = [('flags', ctypes.c_uint32)]


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.hooks = ModuleType('windows_mcp.desktop.control_hooks')
        self.original_key = self.hooks.physical_key = Mock(return_value=90)
        self.original_mouse = self.hooks.physical_mouse = Mock(return_value=91)
        self.original_raw = self.hooks.raw_input = Mock()
        self.hooks._KeyHookData = KeyData
        self.hooks._MouseHookData = MouseData
        class Desktop:
            def get_displays(self):
                return [SimpleNamespace(index=1, primary=True)]
            def get_state(self, use_vision=False, display_indices=None, region=None):
                return display_indices, region
        self.Desktop = Desktop
        desktop = ModuleType('windows_mcp.desktop')
        desktop.control_hooks = self.hooks
        desktop.control_overlay = SimpleNamespace(_notice_bitmap=Mock())
        desktop.control_overlay_art = SimpleNamespace(_notice_bitmap=Mock())
        self.desktop_module = desktop
        self.snapshot = SimpleNamespace(register=Mock())
        self.original_register = self.snapshot.register
        package = ModuleType('windows_mcp')
        package.desktop = desktop
        self.modules = patch.dict(sys.modules, {
            'windows_mcp': package, 'windows_mcp.desktop': desktop,
            'windows_mcp.desktop.service': SimpleNamespace(Desktop=Desktop),
            'windows_mcp.tools': SimpleNamespace(snapshot=self.snapshot),
        })
        self.modules.start()
        install()
        self.owner = SimpleNamespace(_state='ai', _emergency=False, _suppress=True,
            _visual_paused=False, _deadline=time.monotonic()+10, _generation=3,
            _fail_open=Mock(), _queue=Mock(), input_ledger=SimpleNamespace(block_new=Mock()))

    def tearDown(self):
        self.modules.stop()

    def key(self, vk, down=True, flags=0):
        data = KeyData(vkCode=vk, flags=flags)
        return self.hooks.physical_key(self.owner, 0, 0x100 if down else 0x101, ctypes.addressof(data))

    def test_ordinary_keys_mouse_and_raw_do_not_preempt(self):
        self.assertEqual(self.key(65), 1)
        self.assertEqual(self.key(65, False), 1)
        data = MouseData(flags=0)
        self.assertEqual(self.hooks.physical_mouse(self.owner, 0, 0x200, ctypes.addressof(data)), 1)
        self.hooks.raw_input(self.owner, 123)
        self.owner._queue.assert_not_called()
        self.owner.input_ledger.block_new.assert_not_called()
        self.original_raw.assert_not_called()

    def test_injected_events_continue_to_upstream(self):
        self.assertEqual(self.key(27, flags=0x10), 90)
        data = MouseData(flags=1)
        self.assertEqual(self.hooks.physical_mouse(self.owner, 0, 0x200, ctypes.addressof(data)), 91)
        self.owner._queue.assert_not_called()

    def test_double_escape_blocks_new_ai_steps_and_queues_handover(self):
        self.key(27)
        self.key(27)  # Repeat from a held key is not the second tap.
        self.owner._queue.assert_not_called()
        self.key(27, False)
        self.key(27)
        self.assertTrue(self.owner._fast_takeover)
        self.assertFalse(self.owner._suppress)
        self.owner.input_ledger.block_new.assert_called_once()
        self.owner._queue.assert_called_once_with(('hotkey',))
        self.owner._state = 'user'
        self.assertEqual(self.key(27, False), 1)
        self.assertEqual(self.key(65), 90)

    def test_capture_suspension_still_ignores_input_and_accepts_escape(self):
        self.owner._suppress = False
        self.owner._visual_paused = True
        self.key(65)
        self.key(65, False)
        self.owner._queue.assert_not_called()
        self.key(27)
        self.key(27, False)
        self.key(27)
        self.owner._queue.assert_called_once_with(('hotkey',))

    def test_expired_deadline_and_fault_release_to_upstream(self):
        self.owner._deadline = 0
        self.assertEqual(self.key(65), 90)
        self.owner._fail_open.assert_called_once()
        self.owner._emergency = True
        self.assertEqual(self.key(66), 90)

    def test_upstream_behavior_outside_active_lease(self):
        self.owner._state = 'ready'
        self.assertEqual(self.key(65), 90)
        self.hooks.raw_input(self.owner, 123)
        self.original_raw.assert_called_once_with(self.owner, 123)

    def test_capture_defaults_overrides_and_banner(self):
        desktop = self.Desktop()
        self.assertEqual(desktop.get_state(), ([1], None))
        self.assertEqual(desktop.get_state(display_indices=[]), ([], None))
        self.assertEqual(desktop.get_state(display_indices=[0]), ([0], None))
        region = [1, 2, 100, 200]
        self.assertEqual(desktop.get_state(region=region), (None, region))
        self.assertIsNone(self.desktop_module.control_overlay._notice_bitmap(1920))

    def test_tool_help_reports_the_actual_default(self):
        mcp = SimpleNamespace(tool=Mock())
        self.snapshot.register(mcp, get_desktop=None, get_analytics=None)
        proxy = self.original_register.call_args.args[0]
        proxy.tool(name='Snapshot', description='omit it to keep the default full-desktop behavior')
        description = mcp.tool.call_args.kwargs['description']
        self.assertIn('primary display', description)
        self.assertNotIn('default full-desktop', description)


if __name__ == '__main__':
    unittest.main()
