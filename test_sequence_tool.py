"""Sequence contracts without any desktop input or native hooks."""
import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sequence_tool import execute, fill_form, install, validate


def click(loc, button='left', clicks=1):
    pass


class SequenceTests(unittest.TestCase):
    def run_plan(self, actions, tools=None, checkpoint=None, **kwargs):
        return asyncio.run(execute(actions, tools or self.tools,
                                   checkpoint or self.checkpoint, **kwargs))

    def setUp(self):
        self.events = []
        async def type_text(loc, text, clear=False, caret_position='idle', press_enter=False):
            self.events.append(('Type', loc, text))
        async def shortcut(shortcut):
            self.events.append(('Shortcut', shortcut))
        self.tools = {'Click': click, 'Type': type_text, 'Shortcut': shortcut}
        self.checkpoint = Mock()
        self.actions = [
            {'name': 'Type', 'arguments': {'loc': [10, 20], 'text': 'test'}},
            {'name': 'Shortcut', 'arguments': {'shortcut': 'tab'}},
        ]

    def test_upstream_coroutines_run_in_order_and_return_no_entered_text(self):
        result = self.run_plan(self.actions)
        self.assertEqual(self.events, [('Type', [10, 20], 'test'), ('Shortcut', 'tab')])
        self.assertEqual(result['completed'], 2)
        self.assertNotIn('test', str(result))
        self.assertEqual(self.checkpoint.call_count, 3)

    def test_entire_plan_is_validated_before_any_action(self):
        invalid = [
            {'name': 'Shell', 'arguments': {'command': 'something'}},
            {'name': 'Type', 'arguments': {'loc': [10, 20], 'text': 'x', 'clear': 'true'}},
            {'name': 'Click', 'arguments': {'loc': [True, 2]}},
            {'name': 'Click', 'arguments': {'loc': [1, 2], 'clicks': 100}},
            {'name': 'Type', 'arguments': {'label': 1, 'text': 'x'}},
            {'name': 'Type', 'arguments': {'loc': [1, 2], 'text': 'x', 'ctx': {}}},
            {'name': 'Type', 'arguments': {'loc': [1, 2], 'text': '\ud800'}},
        ]
        for action in invalid:
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.run_plan([self.actions[0], action])
            self.assertEqual(self.events, [])
        self.checkpoint.assert_not_called()

    def test_incompatible_upstream_signature_fails_before_input(self):
        self.tools['Shortcut'] = lambda unexpected: None
        with self.assertRaises(TypeError):
            self.run_plan(self.actions)
        self.assertEqual(self.events, [])

    def test_bounds_apply_before_input(self):
        for actions in ([], self.actions * 9,
                        [{'name': 'Type', 'arguments': {'loc': [1, 2], 'text': 'x' * 4001}}]):
            with self.assertRaises(ValueError):
                self.run_plan(actions)
        for seconds in (True, 0, 31, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.run_plan(self.actions, max_seconds=seconds)
        self.assertEqual(self.events, [])

    def test_first_error_is_preserved_and_later_input_is_never_retried(self):
        class ControlBlocked(Exception):
            pass
        error = ControlBlocked('USER_CONTROL')
        async def failing(**kwargs):
            self.events.append('failed once')
            raise error
        self.tools['Type'] = failing
        with self.assertRaises(ControlBlocked) as caught:
            self.run_plan(self.actions)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.events, ['failed once'])
        self.assertIn('0 prior actions completed', error.__notes__[0])

    def test_takeover_between_actions_stops_second_action(self):
        self.checkpoint.side_effect = [None, RuntimeError('takeover')]
        with self.assertRaisesRegex(RuntimeError, 'takeover'):
            self.run_plan(self.actions)
        self.assertEqual(len(self.events), 1)

    def test_budget_never_starts_another_action(self):
        times = iter([0, 0, 0, 1, 31])
        with self.assertRaisesRegex(TimeoutError, '1 actions completed'):
            self.run_plan(self.actions, clock=lambda: next(times))
        self.assertEqual(len(self.events), 1)

    def test_install_is_opt_in_idempotent_and_does_not_replace_gate(self):
        modules = []
        upstream = SimpleNamespace(_MODULES=modules)
        with patch.dict(sys.modules, {'windows_mcp': SimpleNamespace(tools=upstream)}):
            install()
            install()
        self.assertEqual(modules, [sys.modules['sequence_tool']])


class FillFormTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.checkpoint = Mock()
        async def original_type(loc, text, clear=False, press_enter=False):
            self.calls.append(dict(loc=loc, text=text, clear=clear, press_enter=press_enter))
        self.original_type = original_type

    def fill(self, fields, loc=None, **kwargs):
        return asyncio.run(fill_form([1, 2] if loc is None else loc, fields,
                                    self.original_type, self.checkpoint, **kwargs))

    def test_default_keeps_focus_on_last_field_without_submit(self):
        result = self.fill(['Ada', 'Lovelace', 'ada@example.com'])
        self.assertEqual(self.calls, [dict(loc=[1, 2], text='Ada\tLovelace\tada@example.com',
                                          clear=False, press_enter=False)])
        self.assertEqual(result['fields_sent'], 3)
        self.assertFalse(result['submit_requested'])
        self.assertEqual(self.checkpoint.call_count, 2)
        self.assertNotIn('ada@example.com', str(result))

    def test_explicit_submit_tabs_to_next_control_then_enters(self):
        self.fill(['Ada', 'Lovelace'], submit=True)
        self.assertEqual(self.calls[0]['text'], 'Ada\tLovelace\t')
        self.assertTrue(self.calls[0]['press_enter'])

    def test_empty_values_and_unicode_are_preserved(self):
        self.fill(['', '🎯', 'İstanbul'])
        self.assertEqual(self.calls[0]['text'], '\t🎯\tİstanbul')

    def test_preflights_every_field_before_any_input(self):
        for bad in ([], ['x'] * 17, ['x', 1], ['x', 'a\tb'], ['x', 'a\rb'],
                    ['x', 'a\nb'], ['x', '\0'], ['x', '\ud800'], ['x' * 4001]):
            with self.subTest(fields=repr(bad)), self.assertRaises(ValueError):
                self.fill(bad)
        self.assertEqual(self.calls, [])
        self.checkpoint.assert_not_called()

    def test_bad_coordinate_submit_or_signature_prevents_input(self):
        for loc in ([True, 2], [1], '1,2'):
            with self.assertRaises(ValueError):
                self.fill(['x'], loc=loc)
        with self.assertRaises(ValueError):
            self.fill(['x'], submit='true')
        self.original_type = lambda wrong: None
        with self.assertRaises(TypeError):
            self.fill(['x'])
        self.checkpoint.assert_not_called()

    def test_takeover_and_upstream_errors_preserve_identity_without_retry(self):
        error = RuntimeError('control preempted')
        self.checkpoint.side_effect = error
        with self.assertRaises(RuntimeError) as caught:
            self.fill(['x'])
        self.assertIs(caught.exception, error)
        self.assertEqual(self.calls, [])
        self.checkpoint.side_effect = None
        async def fail(**kwargs):
            self.calls.append('attempted once')
            raise error
        self.original_type = fail
        with self.assertRaises(RuntimeError) as caught:
            self.fill(['x'])
        self.assertIs(caught.exception, error)
        self.assertEqual(self.calls, ['attempted once'])


if __name__ == '__main__':
    unittest.main()
