"""Offline input protocol checks; these never inject desktop input."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from rapid_input import KeyEvent, key_pair, text_chunks, send_batch, type_text


class Ledger:
    """Faithful minimal ledger semantics without Windows dependencies."""
    def __init__(self):
        self.holds = {}
        self.blocked = False

    def press(self, name, down, up, physical):
        if self.blocked:
            raise RuntimeError('blocked')
        self.holds[name] = up
        try:
            down()
        except BaseException:
            self.release(name)
            raise

    def release(self, name):
        self.holds[name]()
        del self.holds[name]


class RapidInputTests(unittest.TestCase):
    def setUp(self):
        self.owner = SimpleNamespace(input_ledger=Ledger(), checkpoint_current=Mock(),
                                     record_step_current=Mock(), physical_key_down=Mock(return_value=False))
        self.calls = []

        def sender(events):
            self.calls.append(list(events))
            return len(events)

        self.sender = sender

    def test_utf16_literal_braces_and_emoji(self):
        events = list(text_chunks('{😀}'))[0]
        self.assertEqual([event.scan for event in events[::2]], [123, 0xD83D, 0xDE00, 125])
        self.assertTrue(all(event.flags == 4 for event in events[::2]))
        self.assertTrue(all(event.flags == 6 for event in events[1::2]))

    def test_chunks_do_not_split_surrogates(self):
        chunks = list(text_chunks('a😀b', 1))
        self.assertEqual([len(chunk) for chunk in chunks], [2, 4, 2])

    def test_newline_tab_cr_upstream_semantics(self):
        chunks = list(text_chunks('a\r\nb\tc'))
        self.assertEqual(len(chunks), 5)
        self.assertEqual(chunks[1], key_pair(13))
        self.assertEqual(chunks[3], key_pair(9))

    def test_balanced_success_needs_no_cleanup_input(self):
        send_batch(self.owner, self.sender, key_pair(65))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.owner.input_ledger.holds, {})
        self.owner.record_step_current.assert_called_once()

    def test_partial_injection_releases_only_unmatched_down_and_never_retries(self):
        count = 0

        def sender(events):
            nonlocal count
            count += 1
            self.calls.append(list(events))
            return 3 if count == 1 else len(events)

        with self.assertRaisesRegex(OSError, '3/4'):
            send_batch(self.owner, sender, key_pair(65) + key_pair(66))
        self.assertEqual(self.calls[1], [KeyEvent(vk=66, flags=2)])
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(self.owner.input_ledger.holds)

    def test_zero_insertion_no_extra_release_or_record(self):
        with self.assertRaises(OSError):
            send_batch(self.owner, lambda events: 0, key_pair(65))
        self.owner.record_step_current.assert_not_called()
        self.assertFalse(self.owner.input_ledger.holds)

    def test_unknown_progress_releases_possible_keys(self):
        count = 0

        def sender(events):
            nonlocal count
            count += 1
            if count == 1:
                raise OSError('native exception')
            self.calls.append(list(events))
            return len(events)

        with self.assertRaisesRegex(OSError, 'native exception'):
            send_batch(self.owner, sender, key_pair(65))
        self.assertEqual(self.calls, [[KeyEvent(vk=65, flags=2)]])

    def test_checkpoint_cancellation_injects_nothing(self):
        self.owner.checkpoint_current.side_effect = RuntimeError('cancelled')
        with self.assertRaises(RuntimeError):
            send_batch(self.owner, self.sender, key_pair(65))
        self.assertEqual(self.calls, [])

    def test_blocked_ledger_injects_nothing(self):
        self.owner.input_ledger.blocked = True
        with self.assertRaises(RuntimeError):
            send_batch(self.owner, self.sender, key_pair(65))
        self.assertEqual(self.calls, [])

    def run_type(self, text, **kwargs):
        desktop = SimpleNamespace(_validate_screen_point=Mock())
        focused = kwargs.pop('focused_control', None)
        uia = SimpleNamespace(Click=Mock(), GetFocusedControl=Mock(return_value=focused))
        type_text(desktop, (1, 2), text, owner=self.owner, uia=uia,
                  sender=self.sender, as_bool=lambda value, name: bool(value), **kwargs)
        return desktop, uia

    def test_clear_caret_and_enter_order(self):
        desktop, uia = self.run_type('hi', caret_position='end', clear=True, press_enter=True)
        uia.Click.assert_called_once_with(1, 2)
        desktop._validate_screen_point.assert_called_once_with(1, 2)
        self.assertEqual(self.calls[0], key_pair(35))
        self.assertEqual(self.calls[1], [KeyEvent(vk=17), *key_pair(65),
                                        KeyEvent(vk=17, flags=2), *key_pair(8)])
        self.assertEqual(self.calls[-1], key_pair(13))
        self.assertEqual(len(self.calls), 4)

    def test_long_text_has_bounded_cancellation_points(self):
        self.run_type('x' * 100, chunk_size=32)
        self.assertEqual([len(batch) for batch in self.calls], [64, 64, 64, 8])
        self.assertEqual(self.owner.checkpoint_current.call_count, 6)

    def test_malformed_unicode_is_rejected_before_mutations(self):
        with self.assertRaises(UnicodeEncodeError):
            self.run_type('\ud800', clear=True)
        self.assertEqual(self.calls, [])
        self.owner.record_step_current.assert_not_called()

    def test_foreground_change_stops_remaining_text(self):
        foreground = Mock(side_effect=[100, 100, 200])
        with self.assertRaisesRegex(RuntimeError, 'Foreground window changed'):
            self.run_type('abcdef', chunk_size=3, foreground=foreground)
        self.assertEqual(len(self.calls), 1)

    def test_absent_foreground_stops_before_typing(self):
        with self.assertRaisesRegex(RuntimeError, 'Foreground window changed'):
            self.run_type('abc', foreground=lambda: 0)
        self.assertEqual(self.calls, [])

    def test_explicit_focus_wait_only_changes_post_click_settling(self):
        _, uia = self.run_type('abc', focus_wait=0.05)
        uia.Click.assert_called_once_with(1, 2, waitTime=0.05)

    def test_invalid_focus_wait_is_rejected_before_mutations(self):
        with self.assertRaisesRegex(ValueError, 'focus_wait'):
            self.run_type('abc', focus_wait=0)
        self.assertEqual(self.calls, [])
        self.owner.record_step_current.assert_not_called()

    def test_partial_clear_modifier_cleanup(self):
        count = 0

        def sender(events):
            nonlocal count
            count += 1
            self.calls.append(list(events))
            return 1 if count == 1 else len(events)

        ctrl = KeyEvent(vk=17)
        with self.assertRaises(OSError):
            send_batch(self.owner, sender, [ctrl, *key_pair(65), ctrl.up()])
        self.assertEqual(self.calls[-1], [ctrl.up()])

    def test_failed_cleanup_retry_does_not_release_user_owned_modifier(self):
        count = 0
        def sender(events):
            nonlocal count
            count += 1
            self.calls.append(list(events))
            return 1 if count == 1 else 0
        ctrl = KeyEvent(vk=17)
        with self.assertRaises(OSError):
            send_batch(self.owner, sender, [ctrl, *key_pair(65), ctrl.up()])
        self.assertIn('quickcontrol:input-batch', self.owner.input_ledger.holds)
        self.owner.physical_key_down.return_value = True
        self.owner.input_ledger.release('quickcontrol:input-batch')
        self.assertEqual(count, 2)  # No third Ctrl-up after physical takeover.
        self.assertFalse(self.owner.input_ledger.holds)

    def test_unicode_cleanup_is_not_skipped_for_a_physical_key(self):
        self.owner.physical_key_down.return_value = True
        count = 0
        def sender(events):
            nonlocal count
            count += 1
            self.calls.append(list(events))
            return 1 if count == 1 else len(events)
        events = list(text_chunks('a'))[0]
        with self.assertRaises(OSError):
            send_batch(self.owner, sender, events)
        self.assertEqual(self.calls[-1], [events[0].up()])
        self.owner.physical_key_down.assert_not_called()

    def make_control(self, values, rect=None):
        value_reader = Mock(side_effect=values)

        class Pattern:
            @property
            def Value(self):
                return value_reader()

        return SimpleNamespace(
            BoundingRectangle=rect or SimpleNamespace(left=0, top=0, right=100, bottom=100),
            GetValuePattern=lambda: Pattern())

    def test_focused_verification_matches_exact_value(self):
        control = self.make_control(['', 'abc'])
        self.run_type('abc', focused_control=control, verify_focus=True)
        self.assertEqual(len(self.calls), 1)

    def test_focused_verification_accepts_multiline_crlf_value(self):
        control = self.make_control(['old', 'a\r\nb'])
        self.run_type('a\nb', clear=True, focused_control=control, verify_focus=True)
        self.assertEqual(len(self.calls), 4)

    def test_focused_verification_wrong_target_stops_before_text(self):
        control = self.make_control([''], SimpleNamespace(left=50, top=50, right=100, bottom=100))
        with self.assertRaisesRegex(RuntimeError, 'Focused field could not be verified'):
            self.run_type('abc', focused_control=control, verify_focus=True,
                          verification_timeout=0.001)
        self.assertEqual(self.calls, [])

    def test_focused_verification_unreadable_stops_before_text(self):
        with self.assertRaisesRegex(RuntimeError, 'Focused field could not be verified'):
            self.run_type('abc', verify_focus=True, verification_timeout=0.001)
        self.assertEqual(self.calls, [])

    def test_focused_verification_nonempty_requires_clear(self):
        with self.assertRaisesRegex(RuntimeError, 'requires clear=True'):
            self.run_type('abc', verify_focus=True, focused_control=self.make_control(['old']))
        self.assertEqual(self.calls, [])

    def test_focused_verification_mismatch_fails_without_retry(self):
        control = self.make_control(['', 'wrong', 'wrong', 'wrong'])
        with self.assertRaisesRegex(RuntimeError, 'value did not match'):
            self.run_type('abc', focused_control=control, verify_focus=True,
                          verification_timeout=0.001)
        self.assertEqual(len(self.calls), 1)

    def test_focused_verification_tab_preflight_no_mutation(self):
        with self.assertRaisesRegex(ValueError, 'Tab traversal'):
            self.run_type('a\tb', verify_focus=True)
        self.assertEqual(self.calls, [])
        self.owner.record_step_current.assert_not_called()


if __name__ == '__main__':
    unittest.main()
