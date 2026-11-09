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
    def test_variables_fill_templates_and_keep_originals(self):
        steps = agent_tools.prepare([{'fill': 'Name', 'text': 'Hi {{who}}'}], {'who': 'Ada'})
        self.assertEqual(steps[0]['text'], 'Hi Ada')
        self.assertEqual(steps[0]['_template'], 'Hi {{who}}')

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
            record(s)
            return 'ok'
        folder = Path(tempfile.mkdtemp())
        try:
            with mock.patch.object(agent_tools, 'browser_step', step), \
                 mock.patch.object(agent_tools.BROWSER, 'connect', lambda: None), \
                 mock.patch.object(agent_tools, 'ROUTINES', folder):
                failed, log, _ = agent_tools.run_plan(
                    [{'click': 'a'}, {'click': 'bad'}, {'click': 'c'}], kind='browser',
                    checkpoint=lambda: None, get_desktop=None, save_as='x')
            self.assertEqual(failed, 1)
            self.assertEqual(len(calls), 2)
            self.assertIn('FAILED: covered', log[1])
            self.assertFalse((folder / 'x.json').exists())
        finally:
            shutil.rmtree(folder)

    def test_successful_plan_saves_routine_with_variables(self):
        def step(page, s, record):
            record({'fill': {'text': 'Name'}, 'text': s.get('_template', s.get('text'))})
            return 'ok'
        folder = Path(tempfile.mkdtemp())
        try:
            with mock.patch.object(agent_tools, 'browser_step', step), \
                 mock.patch.object(agent_tools.BROWSER, 'connect', lambda: None), \
                 mock.patch.object(agent_tools, 'ROUTINES', folder):
                steps = agent_tools.prepare([{'fill': 'Name', 'text': '{{who}}'}], {'who': 'Ada'})
                failed, _, _ = agent_tools.run_plan(steps, kind='browser', checkpoint=lambda: None,
                                                    get_desktop=None, save_as='sign up')
                saved = json.loads((folder / 'sign-up.json').read_text(encoding='utf-8'))
                self.assertIsNone(failed)
                self.assertEqual(saved['vars'], ['who'])
                self.assertEqual(saved['steps'][0]['text'], '{{who}}')
                self.assertIn('sign-up(who)', agent_tools.routine_names()[0])
        finally:
            shutil.rmtree(folder)

    def test_checkpoint_runs_before_every_step(self):
        order = []
        with mock.patch.object(agent_tools, 'browser_step', lambda p, s, r: order.append('step') or 'ok'), \
             mock.patch.object(agent_tools.BROWSER, 'connect', lambda: None):
            agent_tools.run_plan([{'click': 1}, {'click': 2}], kind='browser',
                                 checkpoint=lambda: order.append('check'), get_desktop=None)
        self.assertEqual(order, ['check', 'step', 'check', 'step'])

    def test_string_targets_become_label_targets(self):
        self.assertEqual(agent_tools._target({'click': 'Login'}, 'click'), {'text': 'Login'})
        self.assertEqual(agent_tools._target({'click': '7'}, 'click'), 7)
        self.assertEqual(agent_tools._target({'click': {'text': 'Add', 'near': 'Onesie', 'x': 1}}, 'click'),
                         {'text': 'Add', 'near': 'Onesie'})


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
