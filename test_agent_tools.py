"""Offline tests for Observe/Act plumbing. No browser or desktop is touched."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent_tools
import qc_browser
import qc_desktop


class FakePage:
    def __init__(self):
        self.sent = []

    def send(self, method, params=None, timeout=15):
        self.sent.append((method, params))
        return {}


class PlanTests(unittest.TestCase):
    def plan(self, steps, step_fn, variables=None, save_as=None, folder=None):
        with mock.patch.object(agent_tools, 'browser_step', step_fn), \
             mock.patch.object(agent_tools.BROWSER, 'connect', lambda: None), \
             mock.patch.object(agent_tools, 'ROUTINES', folder or Path(tempfile.gettempdir())):
            return agent_tools.run_plan(agent_tools.prepare(steps, variables or {}), kind='browser',
                                        checkpoint=lambda: None, get_desktop=None, save_as=save_as)

    def saved(self, steps, step_fn, variables=None):
        folder = Path(tempfile.mkdtemp())
        try:
            failed, _, _ = self.plan(steps, step_fn, variables, 'r', folder)
            self.assertIsNone(failed)
            return json.loads((folder / 'r.json').read_text(encoding='utf-8'))
        finally:
            shutil.rmtree(folder)

    def test_variables_fill_values_and_keep_originals(self):
        steps = agent_tools.prepare([{'fill': 'Name', 'text': 'Hi {{who}}'}], {'who': 'Ada'})
        self.assertEqual(steps[0]['text'], 'Hi Ada')
        self.assertEqual(steps[0]['_orig']['text'], 'Hi {{who}}')

    def test_plan_bounds(self):
        with self.assertRaises(ValueError):
            agent_tools.prepare([], {})
        with self.assertRaises(ValueError):
            agent_tools.prepare([{}] * (agent_tools.MAX_STEPS + 1), {})
        with self.assertRaises(ValueError):
            agent_tools.prepare(['click'], {})

    def test_failure_stops_plan_and_never_saves(self):
        calls = []

        def step(page, s, record):
            calls.append(s)
            if s.get('click') == 'bad':
                raise RuntimeError('covered')
            record(None)
            return 'ok'
        folder = Path(tempfile.mkdtemp())
        try:
            failed, log, _ = self.plan([{'click': 'a'}, {'click': 'bad'}, {'click': 'c'}], step,
                                      save_as='x', folder=folder)
            self.assertEqual(failed, 1)
            self.assertEqual(len(calls), 2)
            self.assertIn('FAILED: covered', log[1])
            self.assertFalse((folder / 'x.json').exists())
        finally:
            shutil.rmtree(folder)

    def test_routine_keeps_every_placeholder(self):
        steps = [{'goto': 'https://shop.test/{{id}}'}, {'fill': 'Name', 'text': '{{who}}'},
                 {'type': '{{note}}'}, {'wait': {'text': '{{who}}'}}]
        saved = self.saved(steps, lambda p, s, r: r(None) or 'ok', {'id': 7, 'who': 'Ada', 'note': 'hi'})
        self.assertEqual(saved['vars'], ['id', 'note', 'who'])
        self.assertEqual(saved['steps'][0]['goto'], 'https://shop.test/{{id}}')
        self.assertEqual(saved['steps'][2]['type'], '{{note}}')

    def test_routine_keeps_near_and_nth_on_label_targets(self):
        steps = [{'click': {'text': 'Add to cart', 'near': 'Blue shirt', 'nth': 1}}]
        saved = self.saved(steps, lambda p, s, r: r({'text': 'Add to cart', 'role': 'button', 'nth': 0}) or 'ok')
        self.assertEqual(saved['steps'][0]['click'], {'text': 'Add to cart', 'near': 'Blue shirt', 'nth': 1})

    def test_routine_replaces_numbers_with_the_element_used(self):
        steps = [{'click': 9}]
        saved = self.saved(steps, lambda p, s, r: r({'text': 'Add to cart', 'role': 'button', 'nth': 3}) or 'ok')
        self.assertEqual(saved['steps'][0]['click'], {'text': 'Add to cart', 'role': 'button', 'nth': 3})

    def test_routine_names_are_sanitized_for_save_and_load(self):
        self.assertEqual(agent_tools.routine_path('buy shirt').name, 'buy-shirt.json')
        self.assertEqual(agent_tools.routine_path('../../x').name, 'x.json')
        self.assertEqual(agent_tools.routine_path('../../x').parent, agent_tools.ROUTINES)
        with self.assertRaises(ValueError):
            agent_tools.routine_path('../')

    def test_checkpoint_runs_before_every_step(self):
        order = []
        self.plan([{'click': 1}, {'click': 2}], lambda p, s, r: order.append('step') or 'ok')
        self.assertEqual(order, ['step', 'step'])
        order.clear()
        with mock.patch.object(agent_tools, 'browser_step', lambda p, s, r: order.append('step') or 'ok'), \
             mock.patch.object(agent_tools.BROWSER, 'connect', lambda: None):
            agent_tools.run_plan(agent_tools.prepare([{'click': 1}, {'click': 2}], {}), kind='browser',
                                 checkpoint=lambda: order.append('check'), get_desktop=None)
        self.assertEqual(order, ['check', 'step', 'check', 'step'])

    def test_only_integers_are_element_numbers(self):
        self.assertEqual(agent_tools._target({'click': 'Login'}, 'click'), {'text': 'Login'})
        self.assertEqual(agent_tools._target({'click': '7'}, 'click'), {'text': '7'})
        self.assertEqual(agent_tools._target({'click': 7}, 'click'), 7)
        with self.assertRaises(ValueError):
            agent_tools._target({'click': True}, 'click')
        self.assertEqual(agent_tools._target({'click': {'text': 'Add', 'near': 'Onesie', 'x': 1}}, 'click'),
                         {'text': 'Add', 'near': 'Onesie'})


class SettleTests(unittest.TestCase):
    class SlowServerPage:
        """Navigation requested at once, URL changes only after a slow response."""
        def __init__(self, commit_after):
            self.calls, self.commit_after, self.seq, self.main_frame = 0, commit_after, 0, 'main'
            self.events = []

        def eval(self, expression, timeout=15):
            self.calls += 1
            if self.calls == 1:
                self.seq += 1
                self.events.append((self.seq, 'Page.frameRequestedNavigation', {'frameId': 'main'}))
            if self.calls < self.commit_after:
                return ['https://a/', 'complete', 1.0]
            if self.calls == self.commit_after:
                self.seq += 1
                self.events.append((self.seq, 'Page.loadEventFired', {}))
            return ['https://b/', 'complete', 2.0]

        main_events = qc_browser.Page.main_events

    def test_slow_navigation_is_not_mistaken_for_nothing(self):
        page = self.SlowServerPage(commit_after=20)  # about 0.6 s, past the quiet window
        self.assertTrue(qc_browser.settle(page, {'seq': 0, 'state': ['https://a/', 'complete', 1.0]}))
        self.assertGreaterEqual(page.calls, 20)

    def test_no_navigation_returns_after_quiet_window(self):
        class Still:
            seq, events, main_frame = 0, [], 'main'
            main_events = qc_browser.Page.main_events

            def eval(self, expression, timeout=15):
                return ['https://a/', 'complete', 1.0]
        self.assertFalse(qc_browser.settle(Still(), {'seq': 0, 'state': ['https://a/', 'complete', 1.0]}))


class BrowserTests(unittest.TestCase):
    def test_injected_library_is_valid_javascript(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node not installed')
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as handle:
            handle.write(qc_browser.LIB)
        try:
            result = subprocess.run([node, '--check', handle.name], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            Path(handle.name).unlink()

    def test_library_is_versioned(self):
        self.assertNotIn('__QCV__', qc_browser.LIB)

    def test_press_named_key_and_modifier(self):
        page = FakePage()
        qc_browser.press(page, 'Enter')
        self.assertEqual(page.sent[0][1]['text'], '\r')
        page = FakePage()
        qc_browser.press(page, 'ctrl+a')
        self.assertEqual(page.sent[0][1]['commands'], ['selectAll'])
        self.assertEqual(page.sent[0][1]['modifiers'], 2)
        with self.assertRaises(ValueError):
            qc_browser.press(FakePage(), 'hyper+q')

    def test_observation_format(self):
        text = qc_browser.format_observation({
            'title': 'Shop', 'url': 'https://x/', 'scroll': [0, 0], 'viewport': [1, 1], 'text': 'Hello',
            'items': [{'role': 'select', 'name': 'Sort', 'value': 'A', 'options': ['A', 'B']},
                      {'role': 'button', 'name': 'Add', 'ctx': 'Onesie'},
                      {'role': 'input', 'name': 'Zip', 'value': ''},
                      {'role': 'checkbox', 'name': 'Done', 'checked': True}]})
        self.assertIn('[1] select "Sort" value="A" options: A | B', text)
        self.assertIn('[2] button "Add" (for "Onesie")', text)
        self.assertIn('[3] input "Zip" (empty)', text)
        self.assertIn('[4] checkbox "Done" checked', text)
        self.assertTrue(text.endswith('Text: Hello'))


class DesktopTests(unittest.TestCase):
    def elements(self):
        E = qc_desktop.Element
        return [E(None, 'button', 'Clear all memory', '', (0, 0, 1, 1), True, [], ''),
                E(None, 'button', 'Clear', '', (0, 0, 1, 1), True, [], ''),
                E(None, 'edit', 'File name:', '', (0, 0, 1, 1), True, [], '1001')]

    def test_exact_name_beats_prefix(self):
        self.assertEqual(qc_desktop.match(self.elements(), {'name': 'Clear'}).name, 'Clear')

    def test_role_and_automation_id(self):
        self.assertEqual(qc_desktop.match(self.elements(), {'name': '1001', 'role': 'edit'}).role, 'edit')
        with self.assertRaises(LookupError):
            qc_desktop.match(self.elements(), {'name': 'Clear', 'role': 'edit'})

    def test_describe(self):
        text = qc_desktop.describe(self.elements(), ['Display is 0'], 'Calculator')
        self.assertIn('[2] button "Clear"', text)
        self.assertIn('Text: Display is 0', text)


if __name__ == '__main__':
    unittest.main()
