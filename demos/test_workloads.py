import json
from http.server import ThreadingHTTPServer
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from demos.cases import EXPECTED, FORM, actions, verdict
from demos.fixture_server import make_handler
from demos.benchmark import summarize


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def submit(self, run, task, actual):
        request = Request(self.url+'/api/submit', method='POST',
                          headers={'Content-Type': 'application/json'},
                          data=json.dumps({'run': run, 'task': task, 'actual': actual}).encode())
        with urlopen(request) as response:
            return json.load(response)

    def test_unicode_and_extra_text_are_verified_exactly(self):
        self.assertTrue(verdict('form', FORM)['passed'])
        changed = dict(FORM, name='Zoe Garcia')
        self.assertFalse(verdict('form', changed)['passed'])
        self.assertFalse(verdict('form', dict(FORM, extra='unexpected'))['passed'])

    def test_success_is_observable_and_run_cannot_be_reused(self):
        self.assertTrue(self.submit('one-shot', 'edit', EXPECTED['edit'])['passed'])
        with urlopen(self.url+'/api/result?run=one-shot') as response:
            self.assertTrue(json.load(response)['passed'])
        with self.assertRaises(HTTPError) as error:
            self.submit('one-shot', 'edit', EXPECTED['edit'])
        self.assertEqual(error.exception.code, 409)

    def test_failed_outcome_is_preserved(self):
        result = self.submit('failed', 'search', dict(EXPECTED['search'], ticket='QC-101'))
        self.assertFalse(result['passed'])
        self.assertEqual(result['actual']['ticket'], 'QC-101')

    def test_plan_includes_selection_and_submission(self):
        plan = actions('edit')
        self.assertEqual(plan[0], {'type': 'key', 'key': 'ctrl+a'})
        self.assertEqual(plan[-1], {'type': 'key', 'key': 'enter'})
        self.assertEqual(len([a for a in actions('form') if a['type'] == 'text']), 8)

    def test_summary_reports_failures_without_including_them_in_speed(self):
        rows = [{'profile': 'a', 'task': 'form', 'passed': passed, 'verified_ms': timing}
                for passed, timing in [(True, 100), (True, 200), (False, 1)]]
        result = summarize(rows)[0]
        self.assertEqual(result['median_verified_ms'], 150)
        self.assertEqual(result['failed'], 1)


if __name__ == '__main__':
    unittest.main()
