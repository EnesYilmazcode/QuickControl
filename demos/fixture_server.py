"""Serve local synthetic GUI tasks and independently verify their submissions."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlsplit

try:
    from .cases import EXPECTED, EDIT_OLD, verdict
except ImportError:
    from cases import EXPECTED, EDIT_OLD, verdict


def make_handler():
    results = {}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, data, status=200, content_type='application/json'):
            payload = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == '/':
                self.reply(Path(__file__).with_name('workloads.html').read_bytes(), content_type='text/html')
            elif url.path == '/api/cases':
                self.reply({'expected': EXPECTED, 'edit_old': EDIT_OLD})
            elif url.path == '/api/result':
                run = parse_qs(url.query).get('run', [''])[0]
                with lock:
                    result = results.get(run)
                self.reply(result if result is not None else {'run': run, 'submitted': False})
            else:
                self.reply({'error': 'not found'}, 404)

        def do_POST(self):
            if urlsplit(self.path).path != '/api/submit':
                return self.reply({'error': 'not found'}, 404)
            size = int(self.headers.get('Content-Length', '0'))
            if size > 65536:
                return self.reply({'error': 'too large'}, 413)
            try:
                payload = json.loads(self.rfile.read(size))
                run = payload['run']
                if not isinstance(run, str) or not run or len(run) > 200:
                    raise ValueError('Invalid run identifier')
                result = verdict(payload['task'], payload['actual'])
                result.update(run=run, submitted=True, received_ns=time.perf_counter_ns())
                with lock:
                    if run in results:
                        return self.reply({'error': 'run already submitted'}, 409)
                    results[run] = result
                self.reply(result)
            except (ValueError, KeyError, TypeError):
                self.reply({'error': 'invalid submission'}, 400)

    return Handler


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler())
    print(f'Fixture: http://127.0.0.1:{server.server_port}/?task=form&run=manual-1', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
