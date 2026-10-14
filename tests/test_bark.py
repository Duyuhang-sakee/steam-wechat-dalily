import argparse
import io
import json
import os
import tempfile
import threading
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from steam_daily.__main__ import run
from steam_daily.delivery import Bark, deliver_bark, restore_state, save_json, validate_state
from steam_daily.http import FetchError, Http
from steam_daily.report import BEIJING, MODE, snapshot_hash
from test_delivery import Server, journal


def local_http(server, timeout=1):
    http = Http(timeout=timeout)
    real_json = http.json

    def request(url, payload=None, headers=None, **kwargs):
        parts = urlsplit(url)
        return real_json(server.url + parts.path + ('?' + parts.query if parts.query else ''),
                         payload, headers, **kwargs)

    http.json = request
    return http


class BarkDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.state = journal()
        self.state.update(mode=MODE, site_url='https://example.com/steam/',
                          snapshot_sha256='0' * 64, suppressed=False)

    def test_real_post_persists_intent_and_deduplicates_without_storing_key(self):
        def route(method, path, headers, body):
            self.assertEqual((method, path), ('POST', '/push'))
            self.assertIn('application/json', headers['Content-Type'])
            persisted = json.loads(self.path.read_text(encoding='utf-8'))
            self.assertEqual(persisted['bark'], {'status': 'sending', 'attempts': 1})
            payload = json.loads(body)
            self.assertEqual(payload['device_key'], 'DUMMY_BARK_KEY')
            self.assertEqual(payload['title'], 'Steam优惠晨报 ' + self.state['day'])
            self.assertEqual(payload['url'], self.state['site_url'] + '?day=' + self.state['day'])
            self.assertEqual(payload['group'], 'Steam 优惠晨报')
            self.assertEqual(payload['level'], 'active')
            self.assertEqual(payload['isArchive'], '1')
            self.assertNotIn('<', payload['body'])
            return 200, {}, {'code': 200, 'message': 'success', 'timestamp': 1}

        with Server(route) as server:
            client = Bark(local_http(server), 'DUMMY_BARK_KEY')
            deliver_bark(self.state, self.path, client)
            restored = json.loads(self.path.read_text(encoding='utf-8'))
            deliver_bark(restored, self.path, client)
            self.assertEqual(len(server.requests), 1)
        self.assertEqual(restored['bark'], {'status': 'accepted', 'attempts': 1})
        self.assertNotIn('DUMMY_BARK_KEY', self.path.read_text(encoding='utf-8'))
        self.assertNotIn('api.day.app', self.path.read_text(encoding='utf-8'))
        self.assertEqual(self.state['pages'][0]['status'], 'ready')

    def test_timeout_is_uncertain_and_never_retried(self):
        gate = threading.Event()

        def route(*args):
            gate.wait(1)
            return 200, {}, {'code': 200}

        with Server(route) as server:
            client = Bark(local_http(server, timeout=0.05), 'DUMMY_BARK_KEY')
            with self.assertRaises(FetchError):
                deliver_bark(self.state, self.path, client)
            gate.set()
            self.assertEqual(self.state['bark']['status'], 'uncertain')
            with self.assertRaisesRegex(FetchError, 'uncertain previous'):
                deliver_bark(self.state, self.path, client)
            self.assertEqual(len(server.requests), 1)

    def test_http_failure_does_not_retry_post(self):
        with Server(lambda *args: (503, {}, {'code': 500})) as server:
            with self.assertRaises(FetchError):
                deliver_bark(self.state, self.path, Bark(local_http(server), 'DUMMY_BARK_KEY'))
            self.assertEqual(len(server.requests), 1)
        self.assertEqual(json.loads(self.path.read_text())['bark']['status'], 'uncertain')

    def test_invalid_or_backend_failure_response_stops_without_echoing_secrets(self):
        for response in ([], {'code': '200'}, {'code': True}, b'invalid JSON',
                         {'code': 500, 'message': 'DUMMY_BARK_KEY'}):
            with self.subTest(response=response):
                self.state.pop('bark', None)
                with Server(lambda *args: (200, {}, response)) as server:
                    client = Bark(local_http(server), 'DUMMY_BARK_KEY')
                    with self.assertRaises(FetchError) as error:
                        deliver_bark(self.state, self.path, client)
                    self.assertNotIn('DUMMY_BARK_KEY', str(error.exception))
                    self.assertEqual(self.state['bark']['status'], 'uncertain')
                    with self.assertRaises(FetchError):
                        deliver_bark(self.state, self.path, client)
                    self.assertEqual(len(server.requests), 1)

    def test_explicit_json_rejection_allows_only_three_attempts(self):
        with Server(lambda *args: (200, {}, {'code': 400, 'message': 'DUMMY_BARK_KEY'})) as server:
            client = Bark(local_http(server), 'DUMMY_BARK_KEY')
            for _ in range(3):
                with self.assertRaisesRegex(FetchError, 'rejected'):
                    deliver_bark(self.state, self.path, client)
                self.assertEqual(self.state['bark']['status'], 'failed')
            with self.assertRaisesRegex(FetchError, 'exhausted'):
                deliver_bark(self.state, self.path, client)
            self.assertEqual(len(server.requests), 3)
        self.assertNotIn('DUMMY_BARK_KEY', self.path.read_text(encoding='utf-8'))

    def test_crash_after_intent_is_not_resent(self):
        self.state['bark'] = {'status': 'sending', 'attempts': 1}
        client = Mock()
        with self.assertRaisesRegex(FetchError, 'uncertain previous'):
            deliver_bark(self.state, self.path, client)
        client.send.assert_not_called()

    def test_expired_journal_never_sends(self):
        previous = datetime.now(BEIJING) - timedelta(days=1)
        expires = datetime.combine(previous.date() + timedelta(days=1), datetime.min.time(), tzinfo=BEIJING)
        self.state.update(day=previous.strftime('%Y-%m-%d'), captured_at=previous.isoformat(),
                          expires_ms=int(expires.timestamp() * 1000))
        client = Mock()
        with self.assertRaisesRegex(FetchError, 'expired'):
            deliver_bark(self.state, self.path, client)
        client.send.assert_not_called()

    def test_optional_bark_state_is_validated_and_old_journals_remain_valid(self):
        validate_state(self.state)
        for bark in (None, [], {'status': 'delivered', 'attempts': 1},
                     {'status': 'ready', 'attempts': 1}, {'status': 'accepted', 'attempts': 0},
                     {'status': 'sending', 'attempts': True}, {'status': 'failed', 'attempts': 4}):
            with self.subTest(bark=bark):
                with self.assertRaisesRegex(ValueError, 'Bark'):
                    validate_state({**self.state, 'bark': bark})
        with self.assertRaisesRegex(ValueError, 'prepared'):
            deliver_bark(journal(), self.path, Mock())

    def test_server_uses_https_and_key_never_appears_in_endpoint(self):
        client = Bark(Mock(), 'DUMMY_BARK_KEY', 'https://bark.example.com/notify')
        self.assertEqual(client.endpoint, 'https://bark.example.com/notify/push')
        self.assertNotIn('DUMMY_BARK_KEY', client.endpoint)
        for url in ('http://bark.example.com', 'https://user:pass@bark.example.com',
                    'https://bark.example.com?key=secret', 'https://bark.example.com/#secret',
                    'https://bark.example.com:bad'):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, 'BARK_SERVER_URL'):
                Bark(Mock(), 'DUMMY_BARK_KEY', url)


class BarkPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'state/state.json'
        self.args = argparse.Namespace(config='config.json', report_dir=str(self.root / 'reports'),
            state=str(self.path), prepare_send=True, send=False, restore=False,
            site_url='https://example.com/steam/', deployed_url='https://example.com/steam/', snapshot='')
        environment = patch.dict(os.environ, {'PUSHPLUS_TOKEN': '', 'PUSHPLUS_SECRET_KEY': '',
                                               'BARK_DEVICE_KEY': '', 'BARK_SERVER_URL': ''})
        environment.start()
        self.addCleanup(environment.stop)
        with patch('steam_daily.__main__.collect', return_value=([], [], {'total': 0, 'pages': 1}, [])):
            run(self.args)
        self.state = json.loads(self.path.read_text(encoding='utf-8'))
        self.manifest = {'schema': 1, 'captured_at': self.state['captured_at'],
                         'snapshot_sha256': self.state['snapshot_sha256']}
        self.send_args = argparse.Namespace(**{**vars(self.args), 'prepare_send': False, 'send': True})

    def send(self, server, **environment):
        with patch.dict(os.environ, environment), patch('steam_daily.__main__.Http', return_value=local_http(server)):
            run(self.send_args)

    def route(self, method, path, headers, body):
        if method == 'GET':
            return 200, {}, self.manifest
        if path == '/push':
            return 200, {}, {'code': 200}
        self.assertEqual(path, '/send')
        return 200, {}, {'code': 200, 'data': 'DUMMY_RECEIPT'}

    def test_both_channels_send_once_and_reprepare_preserves_their_records(self):
        with Server(self.route) as server:
            self.send(server, PUSHPLUS_TOKEN='DUMMY_WECHAT_KEY', BARK_DEVICE_KEY='DUMMY_BARK_KEY')
            first = json.loads(self.path.read_text(encoding='utf-8'))
            with patch('steam_daily.__main__.collect') as collect:
                run(self.args)
            collect.assert_not_called()
            self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), first)
            self.send(server, PUSHPLUS_TOKEN='DUMMY_WECHAT_KEY', BARK_DEVICE_KEY='DUMMY_BARK_KEY')
            self.assertEqual([path for method, path, _, _ in server.requests if method == 'POST'], ['/send', '/push'])
            self.assertEqual(server.requests[0][0], 'GET')
        self.assertEqual(first['pages'][0]['status'], 'accepted')
        self.assertEqual(first['bark'], {'status': 'accepted', 'attempts': 1})

    def test_enable_bark_after_wechat_already_accepted(self):
        self.state['pages'][0].update(status='accepted', attempts=1, receipt='old-receipt')
        save_json(self.path, self.state)
        with Server(self.route) as server:
            self.send(server, PUSHPLUS_TOKEN='DUMMY_WECHAT_KEY', BARK_DEVICE_KEY='DUMMY_BARK_KEY')
            self.assertEqual([path for method, path, _, _ in server.requests if method == 'POST'], ['/push'])
        self.assertEqual(json.loads(self.path.read_text())['pages'], self.state['pages'])

    def test_one_channel_failure_does_not_block_the_other_or_cause_duplicate_sends(self):
        for failed_path in ('/send', '/push'):
            with self.subTest(failed_path=failed_path):
                save_json(self.path, self.state)

                def route(method, path, headers, body):
                    if method == 'POST' and path == failed_path:
                        return 503, {}, {'code': 500}
                    return self.route(method, path, headers, body)

                with Server(route) as server:
                    for _ in range(2):
                        with self.assertRaises(FetchError):
                            self.send(server, PUSHPLUS_TOKEN='DUMMY_WECHAT_KEY', BARK_DEVICE_KEY='DUMMY_BARK_KEY')
                    self.assertEqual([path for method, path, _, _ in server.requests if method == 'POST'], ['/send', '/push'])
                saved = json.loads(self.path.read_text())
                self.assertEqual(saved['pages'][0]['status'], 'uncertain' if failed_path == '/send' else 'accepted')
                self.assertEqual(saved['bark']['status'], 'uncertain' if failed_path == '/push' else 'accepted')

    def test_bark_only_does_not_require_wechat_credentials(self):
        with Server(self.route) as server:
            self.send(server, BARK_DEVICE_KEY='DUMMY_BARK_KEY')
            self.assertEqual([path for method, path, _, _ in server.requests if method == 'POST'], ['/push'])
        self.assertEqual(json.loads(self.path.read_text())['pages'][0]['attempts'], 0)

    def test_next_day_creates_fresh_bark_and_wechat_notifications(self):
        previous = datetime.now(BEIJING) - timedelta(days=1)
        expires = datetime.combine(previous.date() + timedelta(days=1), datetime.min.time(), tzinfo=BEIJING)
        snapshot_path = self.path.parent / 'offers.json'
        snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))
        snapshot['captured_at'] = previous.isoformat()
        self.state.update(day=previous.strftime('%Y-%m-%d'), captured_at=previous.isoformat(),
                          expires_ms=int(expires.timestamp() * 1000), snapshot_sha256=snapshot_hash(snapshot))
        self.state['pages'][0].update(status='accepted', attempts=1, receipt='old-receipt')
        self.state['bark'] = {'status': 'accepted', 'attempts': 1}
        save_json(snapshot_path, snapshot)
        save_json(self.path, self.state)
        with patch('steam_daily.__main__.collect', return_value=([], [], {'total': 0, 'pages': 1}, [])) as collect:
            run(self.args)
        collect.assert_called_once()
        fresh = json.loads(self.path.read_text())
        self.assertNotEqual(fresh['day'], self.state['day'])
        self.assertEqual(fresh['pages'][0]['attempts'], 0)
        self.assertNotIn('bark', fresh)
        self.manifest.update(captured_at=fresh['captured_at'], snapshot_sha256=fresh['snapshot_sha256'])
        with Server(self.route) as server:
            self.send(server, PUSHPLUS_TOKEN='DUMMY_WECHAT_KEY', BARK_DEVICE_KEY='DUMMY_BARK_KEY')
            self.assertEqual([path for method, path, _, _ in server.requests if method == 'POST'], ['/send', '/push'])

    def test_no_configured_channel_fails_before_network_access(self):
        http = Mock()
        with patch('steam_daily.__main__.Http', return_value=http), self.assertRaisesRegex(ValueError, 'BARK_DEVICE_KEY'):
            run(self.send_args)
        http.json.assert_not_called()

    def test_legacy_wechat_suppression_does_not_suppress_first_bark_push(self):
        self.state['suppressed'] = True
        save_json(self.path, self.state)
        with Server(self.route) as server:
            self.send(server, PUSHPLUS_TOKEN='DUMMY_WECHAT_KEY', BARK_DEVICE_KEY='DUMMY_BARK_KEY')
            self.assertEqual([path for method, path, _, _ in server.requests if method == 'POST'], ['/push'])
        self.assertEqual(json.loads(self.path.read_text())['pages'][0]['attempts'], 0)

    def test_stale_manifest_or_wrong_deployment_blocks_both_channels(self):
        for wrong_url in (False, True):
            with self.subTest(wrong_url=wrong_url):
                self.send_args.deployed_url = 'https://other.example/' if wrong_url else self.args.site_url
                with Server(lambda *args: (200, {}, {'schema': 1, 'snapshot_sha256': '0' * 64})) as server:
                    with patch('steam_daily.__main__.time.sleep'), self.assertRaises((ValueError, FetchError)):
                        self.send(server, PUSHPLUS_TOKEN='DUMMY_WECHAT_KEY', BARK_DEVICE_KEY='DUMMY_BARK_KEY')
                    self.assertFalse(any(method == 'POST' for method, _, _, _ in server.requests))
        self.assertNotIn('bark', json.loads(self.path.read_text()))

    def test_invalid_bark_config_still_allows_wechat(self):
        with Server(self.route) as server:
            with self.assertRaisesRegex(FetchError, 'BARK_SERVER_URL'):
                self.send(server, PUSHPLUS_TOKEN='DUMMY_WECHAT_KEY', BARK_DEVICE_KEY='DUMMY_BARK_KEY',
                          BARK_SERVER_URL='http://insecure.example')
            self.assertEqual([path for method, path, _, _ in server.requests if method == 'POST'], ['/send'])

    def test_restore_artifact_keeps_bark_and_wechat_statuses(self):
        self.state['pages'][0].update(status='accepted', attempts=1, receipt='old-receipt')
        self.state['bark'] = {'status': 'accepted', 'attempts': 1}
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('state.json', json.dumps(self.state))
            output.writestr('offers.json', (self.path.parent / 'offers.json').read_bytes())
        http = Mock()
        http.json.return_value = {'total_count': 1, 'artifacts': [
            {'id': 20, 'name': 'steam-daily-state-100-1', 'expired': False}]}
        http.request.return_value = archive.getvalue()
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_TOKEN': 'DUMMY',
                                     'GITHUB_RUN_ID': '101', 'GITHUB_RUN_ATTEMPT': '1'}):
            restore_state(http, self.path, self.path.parent / 'offers.json')
        with Server(self.route) as server:
            self.send(server, PUSHPLUS_TOKEN='DUMMY_WECHAT_KEY', BARK_DEVICE_KEY='DUMMY_BARK_KEY')
            self.assertFalse(any(method == 'POST' for method, _, _, _ in server.requests))
        self.assertEqual(json.loads(self.path.read_text()), self.state)


if __name__ == '__main__':
    unittest.main()
