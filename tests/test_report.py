import argparse
import io
import json
import os
import re
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from steam_daily.__main__ import run
from steam_daily.delivery import restore_state, save_json, validate_state
from steam_daily.http import FetchError
from steam_daily.report import BEIJING, MODE, build_page_notification, build_site, select_recommendations, site_url, snapshot_hash
from steam_daily.steam import Offer, parse_search, safe_image_url


def product(index, **changes):
    return Offer(**{
        'key': f'app:{index}', 'name': f'Game {index}', 'url': f'https://store.steampowered.com/app/{index}/',
        'discount': 50, 'original': '¥10', 'current': '¥5', 'final_cents': 500, **changes,
    })


def snapshot(freebies=None, discounts=None):
    return {'captured_at': datetime.now(BEIJING).isoformat(), 'country': 'CN', 'minimum_discount': 50,
            'stats': {'total': 100, 'pages': 1}, 'freebies': [item.to_dict() for item in (freebies or [])],
            'discounts': [item.to_dict() for item in (discounts or [])], 'warnings': []}


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(Path('config.json').read_text(encoding='utf-8'))

    def test_all_6000_offers_preserved_in_single_safe_webpage(self):
        offers = [product(i, name=f'Game {i} </script><img src=x onerror=alert(1)> &') for i in range(6000)]
        document = build_site(snapshot(discounts=offers), self.config)
        payload = re.search(r'<script id="offer-data" type="application/json">(.*?)</script>', document, re.S)[1]
        data = json.loads(payload)
        self.assertEqual(len(data['offers']), 6000)
        self.assertEqual({item['key'] for item in data['offers']}, {item.key for item in offers})
        self.assertNotIn('</script>', payload)
        self.assertNotIn('<img', payload)
        self.assertEqual(data['offers'][0]['name'], offers[0].name)
        self.assertNotIn('@@DATA@@', document)
        self.assertNotIn('@@STYLE@@', document)

    def test_recommendations_use_reviews_and_deterministic_discount_fallback(self):
        offers = [product(1, discount=95), product(2, review_positive=95, review_count=600),
                  product(3, review_positive=80, review_count=1000), product(4, review_positive=79, review_count=9999),
                  product(5, review_positive=99, review_count=499), product(6, discount=90)]
        picked = select_recommendations(offers)
        self.assertEqual([item.key for item in picked], ['app:3', 'app:2', 'app:1', 'app:6', 'app:4'])
        self.assertEqual(len({item.key for item in picked}), 5)

    def test_preview_link_opens_the_complete_local_site(self):
        now = datetime.now(BEIJING)
        content = build_page_notification(now)
        self.assertIn('href="site/index.html?day=' + now.strftime('%Y-%m-%d') + '"', content)
        self.assertEqual(content.count('<a '), 1)

    def test_observed_covers_and_chinese_review_data_from_real_fixture(self):
        raw = json.loads(Path('tests/fixtures/steam-search-cn.json').read_text(encoding='utf-8'))
        offers = parse_search(raw['results_html'])
        self.assertTrue(all(item.image_url.startswith('https://') for item in offers))
        self.assertEqual(offers[0].review_positive, 54)
        self.assertEqual(offers[0].review_count, 719)
        self.assertIn('capsule_231x87', offers[0].image_url)

    def test_english_reviews_and_missing_or_unsafe_images(self):
        from test_steam import search_row
        markup = search_row().replace('<span class="title">', '<img src="javascript:alert(1)">'
            '<span class="search_review_summary" data-tooltip-html="Very Positive&lt;br&gt;'
            '95% of the 1,234 user reviews are positive."></span><span class="title">')
        offer = parse_search(markup)[0]
        self.assertEqual(offer.image_url, '')
        self.assertEqual((offer.review_positive, offer.review_count), (95, 1234))
        self.assertEqual(parse_search(search_row())[0].image_url, '')
        for url in ('http://shared.fastly.steamstatic.com/a.jpg', 'https://steamstatic.com.evil.invalid/a.jpg',
                    'https://user:pass@steamstatic.com/a.jpg', 'data:image/svg+xml,test', '//steamstatic.com/a.jpg'):
            self.assertEqual(safe_image_url(url), '')

    def test_https_pages_url_validation(self):
        self.assertEqual(site_url('https://example.com/project'), 'https://example.com/project/')
        for url in ('http://example.com/', 'https://user:pass@example.com/', 'https://example.com/?token=x',
                    'https://example.com/#x', 'https://example.com/<img>', 'https://example.com:bad/'):
            with self.assertRaises(ValueError):
                site_url(url)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.args = argparse.Namespace(config='config.json', report_dir=str(self.root / 'reports'),
            state=str(self.root / 'state/state.json'), prepare_send=True, send=False, restore=False,
            site_url='https://example.com/steam/', deployed_url='', snapshot='', notification_id='')
        self.config = json.loads(Path('config.json').read_text(encoding='utf-8'))
        self.scan = ([], [product(1), product(2)], {'total': 2, 'pages': 1}, [])

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self):
        with patch('steam_daily.__main__.collect', return_value=self.scan):
            run(self.args)
        return json.loads(Path(self.args.state).read_text(encoding='utf-8'))

    def send_args(self):
        return argparse.Namespace(**{**vars(self.args), 'prepare_send': False, 'send': True,
                                      'deployed_url': self.args.site_url, 'notification_id': ''})

    def manifest(self, state):
        return {'schema': 1, 'captured_at': state['captured_at'], 'snapshot_sha256': state['snapshot_sha256']}

    def test_prepare_creates_one_message_and_bound_site_with_no_provider_calls(self):
        with patch('steam_daily.__main__.PushPlus') as provider:
            state = self.prepare()
        provider.assert_not_called()
        self.assertEqual(state['mode'], MODE)
        self.assertEqual(len(state['pages']), 1)
        validate_state(state)
        saved = json.loads((self.root / 'state/offers.json').read_text(encoding='utf-8'))
        self.assertEqual(state['snapshot_sha256'], snapshot_hash(saved))
        document = self.root / 'reports/site/index.html'
        self.assertTrue(document.exists())
        self.assertTrue((self.root / 'reports/wechat-notification.html').exists())
        self.assertEqual(json.loads((document.parent / 'manifest.json').read_text())['snapshot_sha256'],
                         state['snapshot_sha256'])

    def test_send_after_public_manifest_check_and_same_day_deduplication(self):
        state = self.prepare()
        http = Mock()
        http.json.return_value = self.manifest(state)
        client = Mock(secret_key='')
        client.send.return_value = 'receipt'
        client.status.return_value = None
        with patch.dict(os.environ, {'PUSHPLUS_TOKEN': 'DUMMY'}), patch('steam_daily.__main__.Http', return_value=http), \
                patch('steam_daily.__main__.PushPlus', return_value=client):
            run(self.send_args())
            run(self.send_args())
        client.send.assert_called_once()
        self.assertIn('manifest.json?digest=', http.json.call_args.args[0])
        self.assertEqual(json.loads(Path(self.args.state).read_text())['pages'][0]['status'], 'accepted')

    def test_real_send_contains_only_the_published_page_link_and_deduplicates(self):
        from test_delivery import Server
        from steam_daily.http import Http

        state = self.prepare()
        state['pages'][0]['content'] = '<p>OLD DIGEST</p>'
        save_json(self.args.state, state)
        requests = []

        def route(method, url, headers, body):
            if method == 'GET' and url.startswith('/steam/manifest.json?digest='):
                return 200, {}, self.manifest(state)
            payload = json.loads(body)
            requests.append(payload)
            return 200, {}, {'code': 200, 'data': 'page-link-receipt'}

        with Server(route) as server:
            http = Http(timeout=1)
            real_json = http.json

            def local_request(url, payload=None, headers=None, **kwargs):
                from urllib.parse import urlsplit
                parts = urlsplit(url)
                return real_json(server.url + parts.path + ('?' + parts.query if parts.query else ''),
                                 payload, headers, **kwargs)

            http.json = local_request
            with patch.dict(os.environ, {'PUSHPLUS_TOKEN': 'DUMMY', 'PUSHPLUS_SECRET_KEY': ''}), \
                    patch('steam_daily.__main__.Http', return_value=http):
                first_output = io.StringIO()
                rerun_output = io.StringIO()
                with redirect_stdout(first_output):
                    run(self.send_args())
                with redirect_stdout(rerun_output):
                    run(self.send_args())
        self.assertEqual(len(requests), 1)
        self.assertIn('Submitted ONE new page notification in this run', first_output.getvalue())
        self.assertIn('WeChat delivery is not verified', first_output.getvalue())
        self.assertIn('No new notification submitted', rerun_output.getvalue())
        self.assertIn('duplicate send skipped', rerun_output.getvalue())
        self.assertIn('delivery remains unverified', rerun_output.getvalue())
        self.assertEqual(requests[0]['template'], 'html')
        self.assertEqual(requests[0]['channel'], 'wechat')
        content = requests[0]['content']
        self.assertEqual(content.count('<a '), 1)
        self.assertIn(self.args.site_url + '?day=' + state['day'], content)
        self.assertNotIn('Game 1', content)
        self.assertNotIn('折扣精选', content)
        self.assertNotIn('OLD DIGEST', content)

    def test_manual_runs_submit_again_with_real_http_and_same_run_retry_deduplicates(self):
        from test_delivery import Server
        from steam_daily.http import Http

        original = self.prepare()
        original['pages'][0].update(content='<p>OLD SUMMARY</p>', status='accepted',
                                    attempts=1, receipt='old-receipt')
        save_json(self.args.state, original)
        submitted = []

        def route(method, url, headers, body):
            if method == 'GET' and url.startswith('/steam/manifest.json?digest='):
                return 200, {}, self.manifest(original)
            self.assertEqual((method, url), ('POST', '/send'))
            submitted.append(json.loads(body))
            return 200, {}, {'code': 200, 'data': f'manual-receipt-{len(submitted)}'}

        with Server(route) as server:
            http = Http(timeout=1)
            real_json = http.json

            def local_request(url, payload=None, headers=None, **kwargs):
                from urllib.parse import urlsplit
                parts = urlsplit(url)
                return real_json(server.url + parts.path + ('?' + parts.query if parts.query else ''),
                                 payload, headers, **kwargs)

            http.json = local_request
            with patch.dict(os.environ, {'PUSHPLUS_TOKEN': 'DUMMY', 'PUSHPLUS_SECRET_KEY': ''}), \
                    patch('steam_daily.__main__.Http', return_value=http), \
                    patch('steam_daily.__main__.collect') as collect:
                for notification_id, expected_count in (('101', 1), ('101', 1), ('102', 2)):
                    self.args.notification_id = notification_id
                    run(self.args)
                    prepared = json.loads(Path(self.args.state).read_text(encoding='utf-8'))
                    self.assertEqual(prepared['notification_id'], notification_id)
                    self.assertEqual(prepared['snapshot_sha256'], original['snapshot_sha256'])
                    self.assertEqual(prepared['captured_at'], original['captured_at'])
                    run(self.send_args())
                    self.assertEqual(len(submitted), expected_count)
                collect.assert_not_called()
        final = json.loads(Path(self.args.state).read_text(encoding='utf-8'))
        self.assertEqual(final['pages'][0]['receipt'], 'manual-receipt-2')
        self.assertEqual(final['previous_notification']['pages'][0]['receipt'], 'manual-receipt-1')
        for payload in submitted:
            self.assertEqual(payload['content'].count('<a '), 1)
            self.assertIn(self.args.site_url + '?day=' + original['day'], payload['content'])
            self.assertNotIn('OLD SUMMARY', payload['content'])

    def test_manual_run_prepares_new_notification_from_any_previous_status_or_suppression(self):
        for status, suppressed in (('accepted', False), ('delivered', False), ('pending', False),
                                   ('sending', False), ('uncertain', False), ('ready', True)):
            with self.subTest(status=status, suppressed=suppressed):
                self.args.notification_id = ''
                state = self.prepare()
                state['suppressed'] = suppressed
                state['pages'][0].update(content='<p>OLD SUMMARY</p>', status=status, attempts=1)
                if status in ('accepted', 'pending', 'delivered'):
                    state['pages'][0]['receipt'] = 'previous-receipt'
                save_json(self.args.state, state)
                self.args.notification_id = f'manual-{status}-{int(suppressed)}'
                with patch('steam_daily.__main__.collect') as collect:
                    run(self.args)
                collect.assert_not_called()
                prepared = json.loads(Path(self.args.state).read_text(encoding='utf-8'))
                self.assertFalse(prepared['suppressed'])
                self.assertEqual(prepared['pages'][0]['status'], 'ready')
                self.assertEqual(prepared['pages'][0]['attempts'], 0)
                self.assertNotIn('receipt', prepared['pages'][0])
                self.assertNotIn('OLD SUMMARY', prepared['pages'][0]['content'])
                self.assertEqual(prepared['previous_notification']['pages'], state['pages'])
                self.assertEqual(prepared['snapshot_sha256'], state['snapshot_sha256'])

    def test_notification_id_requires_prepare_and_valid_identifier(self):
        for prepare, notification_id in ((False, '101'), (True, '../invalid'), (True, 'x' * 101)):
            with self.subTest(prepare=prepare, notification_id=notification_id):
                args = argparse.Namespace(**{**vars(self.args), 'prepare_send': prepare,
                                              'notification_id': notification_id})
                with patch('steam_daily.__main__.collect') as collect, \
                        patch('steam_daily.__main__.Http') as http:
                    with self.assertRaisesRegex(ValueError, 'notification'):
                        run(args)
                    collect.assert_not_called()
                    http.assert_not_called()

    def test_verified_delivery_log_distinguishes_new_submission_from_previous_receipt(self):
        for status, final_statuses, expected_sends in (
                ('ready', [2], 1), ('accepted', [2], 0), ('pending', [2], 0),
                ('delivered', [], 0), ('accepted', [3, 2], 1)):
            with self.subTest(status=status, final_statuses=final_statuses):
                state = self.prepare()
                page = state['pages'][0]
                page.update(status=status, attempts=0 if status == 'ready' else 1)
                if status != 'ready':
                    page['receipt'] = 'previous-receipt'
                save_json(self.args.state, state)
                http = Mock()
                http.json.return_value = self.manifest(state)
                client = Mock(secret_key='DUMMY')
                client.send.return_value = 'new-receipt'
                client.status.side_effect = final_statuses
                output = io.StringIO()
                with patch.dict(os.environ, {'PUSHPLUS_TOKEN': 'DUMMY', 'PUSHPLUS_SECRET_KEY': 'DUMMY'}), \
                        patch('steam_daily.__main__.Http', return_value=http), \
                        patch('steam_daily.__main__.PushPlus', return_value=client), \
                        patch('steam_daily.delivery.time.sleep'), redirect_stdout(output):
                    run(self.send_args())
                self.assertEqual(client.send.call_count, expected_sends)
                self.assertEqual(json.loads(Path(self.args.state).read_text())['pages'][0]['status'], 'delivered')
                if expected_sends:
                    self.assertIn('Submitted ONE new page notification in this run', output.getvalue())
                    self.assertIn('final WeChat delivery verified', output.getvalue())
                else:
                    self.assertIn('No new notification submitted', output.getvalue())
                    self.assertIn('previous notification is verified delivered', output.getvalue())
                    self.assertIn('duplicate send skipped', output.getvalue())

    def test_wrong_deployment_or_stale_public_snapshot_never_sends(self):
        self.prepare()
        http = Mock()
        http.json.return_value = {'schema': 1, 'snapshot_sha256': '0' * 64}
        for deployed in ('https://different.example/', self.args.site_url):
            args = self.send_args()
            args.deployed_url = deployed
            with patch.dict(os.environ, {'PUSHPLUS_TOKEN': 'DUMMY'}), \
                    patch('steam_daily.__main__.Http', return_value=http), patch('steam_daily.__main__.PushPlus') as provider:
                with self.assertRaises((ValueError, FetchError)):
                    run(args)
                provider.assert_not_called()
        self.assertEqual(json.loads(Path(self.args.state).read_text())['pages'][0]['attempts'], 0)

    def test_same_day_prepare_reuses_saved_snapshot_without_rescanning(self):
        state = self.prepare()
        output = io.StringIO()
        with patch('steam_daily.__main__.collect') as collect, redirect_stdout(output):
            run(self.args)
        collect.assert_not_called()
        self.assertIn('no Steam rescan in this run', output.getvalue())
        self.assertIn(state['captured_at'], output.getvalue())
        self.assertEqual(json.loads(Path(self.args.state).read_text()), state)
        saved = json.loads((self.root / 'state/offers.json').read_text(encoding='utf-8'))
        saved['discounts'][0]['current'] = 'CHANGED'
        save_json(self.root / 'state/offers.json', saved)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            run(self.args)

    def test_old_summary_refreshes_only_unsent_or_confirmed_failed_records(self):
        for status, attempts, receipt, should_refresh in (
                ('ready', 0, None, True), ('failed', 1, 'failed-receipt', True),
                ('accepted', 1, 'receipt', False), ('pending', 1, 'receipt', False),
                ('delivered', 1, 'receipt', False), ('sending', 1, None, False),
                ('uncertain', 1, None, False)):
            with self.subTest(status=status):
                state = self.prepare()
                state['pages'][0] = {'title': 'Old daily notification', 'content': '<p>OLD DIGEST</p>',
                                     'status': status, 'attempts': attempts}
                if receipt:
                    state['pages'][0]['receipt'] = receipt
                save_json(self.args.state, state)
                with patch('steam_daily.__main__.collect') as collect:
                    run(self.args)
                collect.assert_not_called()
                restored = json.loads(Path(self.args.state).read_text(encoding='utf-8'))
                page = restored['pages'][0]
                self.assertEqual('OLD DIGEST' not in page['content'], should_refresh)
                self.assertEqual((page['status'], page['attempts'], page.get('receipt')),
                                 (status, attempts, receipt))
                self.assertEqual(restored['snapshot_sha256'], state['snapshot_sha256'])

    def test_legacy_43_pages_are_stopped_after_any_attempt(self):
        state = self.prepare()
        for field in ('mode', 'site_url', 'snapshot_sha256', 'suppressed'):
            del state[field]
        state['pages'] *= 43
        state['pages'][0] = {**state['pages'][0], 'status': 'accepted', 'attempts': 1, 'receipt': 'legacy-receipt'}
        save_json(self.args.state, state)
        migrated = self.prepare()
        self.assertTrue(migrated['suppressed'])
        self.assertEqual(len(migrated['pages']), 1)
        self.assertEqual(migrated['legacy_sends'][0]['receipt'], 'legacy-receipt')
        with patch('steam_daily.__main__.PushPlus') as provider, patch('steam_daily.__main__.Http') as http:
            run(self.send_args())
            provider.assert_not_called()
            http.return_value.json.assert_not_called()
        save_json(self.args.state, state)
        self.args.notification_id = 'legacy-manual-test'
        manual = self.prepare()
        self.assertFalse(manual['suppressed'])
        self.assertEqual(manual['notification_id'], 'legacy-manual-test')
        self.assertEqual(manual['pages'][0]['status'], 'ready')
        self.assertEqual(manual['legacy_sends'][0]['receipt'], 'legacy-receipt')

    def test_legacy_ready_journal_can_be_replaced_but_never_sent_directly(self):
        state = self.prepare()
        for field in ('mode', 'site_url', 'snapshot_sha256', 'suppressed'):
            del state[field]
        save_json(self.args.state, state)
        with self.assertRaisesRegex(ValueError, 'Legacy'):
            run(self.send_args())
        self.assertFalse(self.prepare()['suppressed'])

    def test_new_journal_restores_bound_snapshot_from_artifact(self):
        state = self.prepare()
        saved = (self.root / 'state/offers.json').read_bytes()
        http = Mock()
        http.json.return_value = {'total_count': 1, 'artifacts': [
            {'id': 20, 'name': 'steam-daily-state-100-1', 'expired': False}]}
        target = self.root / 'restored'
        for corrupt in (False, True):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                archive.writestr('state.json', json.dumps(state))
                archive.writestr('offers.json', saved if not corrupt else saved.replace(b'Game 1', b'Game X'))
            http.request.return_value = buffer.getvalue()
            with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_TOKEN': 'DUMMY',
                                         'GITHUB_RUN_ID': '101', 'GITHUB_RUN_ATTEMPT': '1'}):
                if corrupt:
                    with self.assertRaisesRegex(ValueError, 'does not match'):
                        restore_state(http, target / 'state.json', target / 'offers.json')
                else:
                    restore_state(http, target / 'state.json', target / 'offers.json')
                    self.assertEqual(snapshot_hash(json.loads((target / 'offers.json').read_text(encoding='utf-8'))),
                                     state['snapshot_sha256'])

    def test_preview_from_snapshot_does_not_create_journal_or_scan(self):
        source = self.root / 'source.json'
        save_json(source, snapshot(discounts=[product(1)]))
        args = argparse.Namespace(**{**vars(self.args), 'prepare_send': False, 'snapshot': str(source)})
        with patch('steam_daily.__main__.collect') as collect, patch('steam_daily.__main__.PushPlus') as provider:
            run(args)
        collect.assert_not_called()
        provider.assert_not_called()
        self.assertFalse(Path(self.args.state).exists())
        self.assertTrue((self.root / 'reports/site/index.html').exists())

    def test_single_message_state_rejects_multiple_pages(self):
        state = self.prepare()
        state['pages'] *= 2
        with self.assertRaisesRegex(ValueError, 'single-message'):
            validate_state(state)


if __name__ == '__main__':
    unittest.main()
