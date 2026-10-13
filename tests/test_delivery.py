import io
import json
import os
import tempfile
import threading
import unittest
import zipfile
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from steam_daily.delivery import PushPlus, deliver, restore_state, save_json, validate_state
from steam_daily.http import FetchError, Http
from steam_daily.report import BEIJING, MODE


class Server:
    def __init__(self, route):
        self.requests = []
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def handle_request(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                parent.requests.append((self.command, self.path, dict(self.headers), body))
                code, headers, content = route(self.command, self.path, dict(self.headers), body)
                if not isinstance(content, bytes):
                    content = json.dumps(content).encode('utf-8')
                try:
                    self.send_response(code)
                    for name, value in headers.items():
                        self.send_header(name, value)
                    self.send_header('Content-Length', str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def journal(status='ready', attempts=0, receipt=None):
    now = datetime.now(BEIJING)
    expires = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=BEIJING)
    page = {'title': 'Steam daily', 'content': '<p>Offer</p>', 'status': status, 'attempts': attempts}
    if receipt:
        page['receipt'] = receipt
    return {'schema': 1, 'day': now.strftime('%Y-%m-%d'), 'captured_at': now.isoformat(),
            'expires_ms': int(expires.timestamp() * 1000), 'pages': [page]}


class HttpTests(unittest.TestCase):
    def test_cross_origin_redirect_strips_credentials(self):
        with Server(lambda *args: (200, {}, {'ok': True})) as destination:
            with Server(lambda *args: (302, {'Location': destination.url + '/file'}, b'')) as source:
                result = Http().json(source.url + '/redirect', headers={
                    'Authorization': 'Bearer DUMMY', 'access-key': 'DUMMY', 'Cookie': 'DUMMY', 'Accept': 'application/json'})
                self.assertEqual(result, {'ok': True})
                received = {k.lower(): v for k, v in destination.requests[0][2].items()}
                for key in ('authorization', 'access-key', 'cookie'):
                    self.assertNotIn(key, received)
                self.assertEqual(received['accept'], 'application/json')

    def test_post_redirect_refused_and_not_retried(self):
        with Server(lambda *args: (200, {}, {})) as destination:
            with Server(lambda *args: (302, {'Location': destination.url}, b'')) as source:
                with self.assertRaises(FetchError):
                    Http().json(source.url, {'token': 'DUMMY'})
                self.assertEqual(len(source.requests), 1)
                self.assertEqual(len(destination.requests), 0)

    def test_remote_http_redirect_refused(self):
        with Server(lambda *args: (302, {'Location': 'http://example.invalid/'}, b'')) as source:
            with self.assertRaisesRegex(FetchError, 'Insecure'):
                Http().request(source.url)

    def test_get_retries_but_post_does_not(self):
        with Server(lambda *args: (503, {}, {})) as server:
            with patch('steam_daily.http.time.sleep'):
                with self.assertRaises(FetchError):
                    Http().json(server.url)
                self.assertEqual(len(server.requests), 3)
                with self.assertRaises(FetchError):
                    Http().json(server.url, {'token': 'DUMMY'})
                self.assertEqual(len(server.requests), 4)

    def test_rate_limit_respects_retry_after_and_stops_for_long_delays(self):
        with Server(lambda *args: (429, {'Retry-After': '12'}, {})) as server:
            with patch('steam_daily.http.time.sleep') as sleep:
                with self.assertRaises(FetchError):
                    Http().json(server.url)
                self.assertEqual([call.args[0] for call in sleep.call_args_list], [12, 12])
                self.assertEqual(len(server.requests), 3)
        with Server(lambda *args: (429, {'Retry-After': '120'}, {})) as server:
            with self.assertRaisesRegex(FetchError, 'long retry delay'):
                Http().json(server.url)
            self.assertEqual(len(server.requests), 1)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / 'state.json'

    def tearDown(self):
        self.temporary.cleanup()

    def local_client(self, server, *, secret='', timeout=1):
        http = Http(timeout=timeout)
        real_json = http.json

        def local_request(url, payload=None, headers=None, **kwargs):
            parts = urlsplit(url)
            return real_json(server.url + parts.path + ('?' + parts.query if parts.query else ''), payload, headers, **kwargs)

        http.json = Mock(side_effect=local_request)
        return PushPlus(http, 'DUMMY_TOKEN', secret, gap=0)

    def test_acceptance_is_not_delivery_and_rerun_deduplicates(self):
        def route(method, url, headers, body):
            persisted = json.loads(self.path.read_text(encoding='utf-8'))
            self.assertEqual(persisted['pages'][0]['status'], 'sending')
            self.assertEqual(json.loads(body)['channel'], 'wechat')
            return 200, {}, {'code': 200, 'data': 'receipt-1'}

        with Server(route) as server:
            state = journal()
            client = self.local_client(server)
            deliver(state, self.path, client, poll_seconds=0)
            self.assertEqual(state['pages'][0]['status'], 'accepted')
            self.assertNotEqual(state['pages'][0]['status'], 'delivered')
            restored = json.loads(self.path.read_text(encoding='utf-8'))
            deliver(restored, self.path, client, poll_seconds=0)
            self.assertEqual(len(server.requests), 1)

    def test_pending_receipt_does_not_resend(self):
        def route(method, url, headers, body):
            if 'getAccessKey' in url:
                return 200, {}, {'code': 200, 'data': {'accessKey': 'DUMMY_KEY'}}
            self.assertIn('DUMMY_KEY', headers.values())
            return 200, {}, {'code': 200, 'data': {'status': 1}}

        with Server(route) as server:
            state = journal('accepted', 1, 'receipt-1')
            with self.assertRaisesRegex(FetchError, 'awaiting'):
                deliver(state, self.path, self.local_client(server, secret='DUMMY'), poll_seconds=0)
            self.assertFalse(any(url == '/send' for _, url, _, _ in server.requests))

    def test_final_delivery_confirmed(self):
        def route(method, url, headers, body):
            if url == '/send':
                return 200, {}, {'code': 200, 'data': 'receipt-1'}
            if 'getAccessKey' in url:
                return 200, {}, {'code': 200, 'data': {'accessKey': 'DUMMY_KEY'}}
            return 200, {}, {'code': 200, 'data': {'status': 2}}

        with Server(route) as server:
            state = journal()
            deliver(state, self.path, self.local_client(server, secret='DUMMY'), poll_seconds=0)
            self.assertEqual(state['pages'][0]['status'], 'delivered')
            saved = self.path.read_text(encoding='utf-8')
            self.assertNotIn('DUMMY_TOKEN', saved)
            self.assertNotIn('DUMMY_KEY', saved)

    def test_confirmed_failure_retries_only_on_manual_rerun(self):
        statuses = iter((3, 2))

        def route(method, url, headers, body):
            if url == '/send':
                return 200, {}, {'code': 200, 'data': 'receipt-1'}
            if 'getAccessKey' in url:
                return 200, {}, {'code': 200, 'data': {'accessKey': 'DUMMY_KEY'}}
            return 200, {}, {'code': 200, 'data': {'status': next(statuses)}}

        with Server(route) as server:
            state = journal()
            client = self.local_client(server, secret='DUMMY')
            with self.assertRaisesRegex(FetchError, 'failed final'):
                deliver(state, self.path, client, poll_seconds=0)
            self.assertEqual(state['pages'][0]['status'], 'failed')
            deliver(state, self.path, client, poll_seconds=0)
            self.assertEqual(state['pages'][0]['status'], 'delivered')
            self.assertEqual(state['pages'][0]['attempts'], 2)

    def test_confirmed_failure_of_old_summary_retries_with_page_link(self):
        statuses = iter((3, 2))
        sent = []

        def route(method, url, headers, body):
            if url == '/send':
                sent.append(json.loads(body))
                return 200, {}, {'code': 200, 'data': 'page-link-receipt'}
            if 'getAccessKey' in url:
                return 200, {}, {'code': 200, 'data': {'accessKey': 'DUMMY_KEY'}}
            return 200, {}, {'code': 200, 'data': {'status': next(statuses)}}

        state = journal('accepted', 1, 'old-summary-receipt')
        state.update(mode=MODE, site_url='https://example.com/steam/',
                     snapshot_sha256='0' * 64, suppressed=False)
        state['pages'][0]['content'] = '<p>OLD SUMMARY</p>'
        with Server(route) as server:
            deliver(state, self.path, self.local_client(server, secret='DUMMY'), poll_seconds=0)
        self.assertEqual(len(sent), 1)
        self.assertNotIn('OLD SUMMARY', sent[0]['content'])
        self.assertIn(state['site_url'] + '?day=' + state['day'], sent[0]['content'])
        self.assertEqual(state['pages'][0]['status'], 'delivered')
        self.assertEqual(state['pages'][0]['attempts'], 2)
        self.assertEqual(state['pages'][0]['receipt'], 'page-link-receipt')

    def test_ambiguous_timeout_never_retries_post(self):
        gate = threading.Event()

        def route(*args):
            gate.wait(1)
            return 200, {}, {'code': 200, 'data': 'receipt-1'}

        with Server(route) as server:
            state = journal()
            client = self.local_client(server, timeout=0.05)
            with self.assertRaises(FetchError):
                deliver(state, self.path, client, poll_seconds=0)
            gate.set()
            self.assertEqual(state['pages'][0]['status'], 'uncertain')
            with self.assertRaisesRegex(FetchError, 'uncertain previous'):
                deliver(state, self.path, client, poll_seconds=0)
            self.assertEqual(len(server.requests), 1)

    def test_malformed_provider_response_safe_failure(self):
        for response in ([], {'code': 200, 'data': None}, {'code': 'DUMMY_TOKEN'}):
            with self.subTest(response=response):
                with Server(lambda *args: (200, {}, response)) as server:
                    with self.assertRaises(FetchError) as result:
                        self.local_client(server).send('title', 'body', 123)
                    self.assertNotIn('DUMMY_TOKEN', str(result.exception))

    def test_explicit_rejection_is_failed_and_credentials_not_saved(self):
        with Server(lambda *args: (200, {}, {'code': 905, 'msg': 'DUMMY_TOKEN'})) as server:
            state = journal()
            with self.assertRaises(FetchError):
                deliver(state, self.path, self.local_client(server), poll_seconds=0)
            self.assertEqual(state['pages'][0]['status'], 'failed')
            self.assertNotIn('DUMMY_TOKEN', self.path.read_text(encoding='utf-8'))

    def test_failed_page_has_bounded_attempts(self):
        with self.assertRaisesRegex(FetchError, 'exhausted'):
            deliver(journal('failed', 3), self.path, Mock(), poll_seconds=0)

    def test_state_validation_rejects_missing_receipts_and_bad_content(self):
        for state in ([], journal('accepted', 1), {**journal(), 'expires_ms': 1},
                      {**journal(), 'pages': [{'title': 'x', 'content': 'x' * 18001, 'status': 'ready', 'attempts': 0}]}):
            with self.subTest(state_type=type(state)):
                with self.assertRaises(ValueError):
                    validate_state(state)

    def test_restore_previous_attempt_of_same_run(self):
        state = journal('accepted', 1, 'receipt-1')
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('state.json', json.dumps(state))
        http = Mock()
        http.json.return_value = {'total_count': 2, 'artifacts': [
            {'id': 21, 'name': 'steam-daily-state-100-2', 'expired': False},
            {'id': 20, 'name': 'steam-daily-state-100-1', 'expired': False}]}
        http.request.return_value = archive.getvalue()
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_TOKEN': 'DUMMY',
                                     'GITHUB_RUN_ID': '100', 'GITHUB_RUN_ATTEMPT': '2'}):
            restore_state(http, self.path)
        self.assertIn('/20/zip', http.request.call_args.args[0])
        self.assertEqual(json.loads(self.path.read_text())['pages'][0]['receipt'], 'receipt-1')

    def test_manual_retry_restores_its_own_run_before_a_newer_other_run(self):
        own = journal('accepted', 1, 'own-receipt')
        other = journal('accepted', 1, 'other-receipt')
        archives = {}
        for artifact_id, state in ((20, own), (21, other)):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as output:
                output.writestr('state.json', json.dumps(state))
            archives[artifact_id] = buffer.getvalue()
        http = Mock()
        http.json.return_value = {'total_count': 3, 'artifacts': [
            {'id': 22, 'name': 'steam-daily-state-100-2', 'expired': False},
            {'id': 21, 'name': 'steam-daily-state-101-1', 'expired': False},
            {'id': 20, 'name': 'steam-daily-state-100-1', 'expired': False}]}
        http.request.side_effect = lambda url, **kwargs: archives[int(url.split('/')[-2])]
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_TOKEN': 'DUMMY',
                                     'GITHUB_RUN_ID': '100', 'GITHUB_RUN_ATTEMPT': '2'}):
            restore_state(http, self.path, preferred_run_id='100')
        self.assertEqual(json.loads(self.path.read_text())['pages'][0]['receipt'], 'own-receipt')
        self.assertIn('/20/zip', http.request.call_args.args[0])

    def test_provider_requests_share_rate_limit(self):
        client = PushPlus(Mock(), 'DUMMY', 'DUMMY', gap=13)
        client.http.json.side_effect = [{'code': 200, 'data': 'receipt'},
                                        {'code': 200, 'data': {'accessKey': 'DUMMY'}},
                                        {'code': 200, 'data': {'status': 2}}]
        with patch('steam_daily.delivery.time.monotonic', side_effect=[0, 1, 13, 14, 26]), \
                patch('steam_daily.delivery.time.sleep') as sleep:
            client.send('title', 'body', 123)
            client.status('receipt')
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [12, 12])


if __name__ == '__main__':
    unittest.main()
