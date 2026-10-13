import html
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from steam_daily.__main__ import load_config
from steam_daily.http import FetchError
from steam_daily.report import BEIJING
from steam_daily.steam import Offer, collect, collect_search, confirm_free, parse_search


def search_row(item_id=1, discount=50, cents=100, kind='app', name='Game'):
    return (
        f'<a class="search_result_row" href="https://store.steampowered.com/{kind}/{item_id}/name/?snr=x">'
        f'<span class="title">{html.escape(name)}</span><div class="search_price_discount_combined" data-price-final="{cents}">'
        f'<div class="discount_block search_discount_block" data-price-final="{cents}" data-discount="{discount}">'
        '<div class="discount_original_price">¥2.00</div><div class="discount_final_price">¥1.00</div></div></div></a>'
    )


def response(rows, start=0, total=None):
    return {'success': 1, 'start': start, 'total_count': len(rows) if total is None else total,
            'results_html': ''.join(rows)}


def free_data(key='app:1', *, flag=1, end=None):
    kind, item_id = key.split(':')
    option = {'is_free_to_keep': flag, 'final_price_in_cents': '0'}
    if end is not None:
        option['free_to_keep_ends'] = end
    data = {'rgPayloads': [{'rgStoreItems': [{'id': int(item_id), 'item_type': {'app': 0, 'sub': 1, 'bundle': 2}[kind],
                                            'best_purchase_option': option}]}]}
    return '<div data-props="' + html.escape(json.dumps(data), quote=True) + '"></div>'


class SteamTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(Path('config.json').read_text(encoding='utf-8'))
        self.config.update(page_size=3, request_gap_seconds=0)
        self.now = datetime(2026, 10, 12, 9, 30, tzinfo=BEIJING)

    def offer(self):
        return Offer('app:1', 'Game', 'https://store.steampowered.com/app/1/', 100, '¥2', '免费', 0)

    def test_type_separated_ids_and_escaped_names(self):
        offers = parse_search(''.join(search_row(7, kind=k, name='A & B <C>') for k in ('app', 'sub', 'bundle')))
        self.assertEqual([o.key for o in offers], ['app:7', 'sub:7', 'bundle:7'])
        self.assertEqual(offers[0].name, 'A & B <C>')

    def test_captured_real_steam_cn_search_and_purchase_data(self):
        source = Path(__file__).parent / 'fixtures'
        data = json.loads((source / 'steam-search-cn.json').read_text(encoding='utf-8'))
        offers = parse_search(data['results_html'])
        self.assertEqual(len(offers), 100)
        self.assertEqual(len({offer.key for offer in offers}), 100)
        self.assertEqual(sum(not offer.priced for offer in offers), 3)
        item = json.loads((source / 'steam-free-purchase.json').read_text(encoding='utf-8'))
        props = {'rgPayloads': [{'rgStoreItems': [item]}]}
        text = '<div data-props="' + html.escape(json.dumps(props), quote=True) + '"></div>'
        offer = Offer('app:2283211', 'Ning Hai', 'https://store.steampowered.com/app/2283211/', 100, '¥70', '免费', 0)
        end = item['best_purchase_option']['free_to_keep_ends']
        before = datetime.fromtimestamp(end - 3600, BEIJING)
        self.assertTrue(confirm_free(offer, text, before))
        self.assertEqual(offer.end_epoch, end)
        self.assertFalse(confirm_free(Offer(**{**offer.to_dict(), 'free': False, 'end_epoch': None}), text,
                                      datetime.fromtimestamp(end + 1, BEIJING)))

    def test_threshold_49_50_51_and_zero_price_exclusion(self):
        data = response([search_row(i, d, c) for i, d, c in ((1, 49, 100), (2, 50, 100), (3, 51, 100), (4, 80, 0))])
        self.config['page_size'] = 100
        from unittest.mock import Mock
        http = Mock()
        http.json.return_value = data
        freebies, discounts, _, _ = collect(http, self.config)
        self.assertEqual([o.key for o in discounts], ['app:3', 'app:2'])
        self.assertEqual(freebies, [])
        http.request.assert_not_called()

    def test_explicit_empty_region_price_is_not_free(self):
        for metadata_price in ('0', '4000'):
            text = ('<a class="search_result_row" href="https://store.steampowered.com/app/1/name/">'
                    '<span class="title">Unavailable</span><div class="search_price_discount_combined" '
                    f'data-price-final="{metadata_price}"><div class="search_discount_block discount_block"></div></div></a>')
            offer = parse_search(text)[0]
            self.assertFalse(offer.priced)
            self.assertEqual(offer.discount, 0)
        with self.assertRaises(FetchError):
            parse_search(text.replace('search_price_discount_combined', 'unexpected_structure'))

    def test_full_pagination_and_count(self):
        from unittest.mock import Mock
        http = Mock()
        http.json.side_effect = [response([search_row(i) for i in (1, 2, 3)], total=4),
                                 response([search_row(4)], start=3, total=4)]
        offers, stats = collect_search(http, self.config)
        self.assertEqual(len(offers), 4)
        self.assertEqual(stats, {'total': 4, 'pages': 2, 'unpriced': 0})
        self.assertIn('start=3', http.json.call_args.args[0])
        self.assertIn('sort_by=Name_ASC', http.json.call_args.args[0])

    def test_incomplete_duplicate_and_volatile_search_fail_closed(self):
        from unittest.mock import Mock
        first = response([search_row(i) for i in (1, 2, 3)], total=4)
        cases = [response([], start=3, total=4), response([search_row(3)], start=3, total=4),
                 response([search_row(4)], start=3, total=5), response([search_row(4)], start=0, total=4),
                 {'success': 0}]
        for final in cases:
            with self.subTest(final=final):
                http = Mock()
                http.json.side_effect = [first, final]
                with self.assertRaises(FetchError):
                    collect_search(http, self.config)

    def test_max_pages_never_truncates(self):
        from unittest.mock import Mock
        http = Mock()
        http.json.return_value = response([search_row(i) for i in (1, 2, 3)], total=4)
        self.config['max_pages'] = 1
        with self.assertRaises(FetchError):
            collect_search(http, self.config)

    def test_zero_results(self):
        from unittest.mock import Mock
        http = Mock()
        http.json.return_value = response([])
        self.assertEqual(collect_search(http, self.config)[0], [])

    def test_volatile_scan_restarts_once_without_merging_old_items(self):
        from unittest.mock import Mock
        http = Mock()
        http.json.side_effect = [response([search_row(1)], total=2),
                                 response([search_row(2)], start=1, total=3),
                                 response([search_row(4)], total=1)]
        _, discounts, stats, _ = collect(http, self.config)
        self.assertEqual([o.key for o in discounts], ['app:4'])
        self.assertEqual(stats['total'], 1)
        self.assertEqual(http.json.call_count, 3)
        http.json.side_effect = [response([search_row(1)], total=2),
                                 response([search_row(2)], start=1, total=3)] * 2
        with self.assertRaises(FetchError):
            collect(http, self.config)

    def test_structured_free_uses_matching_item_and_verified_end(self):
        offer = self.offer()
        end = int((self.now + timedelta(hours=12)).timestamp())
        self.assertTrue(confirm_free(offer, free_data(end=end), self.now))
        self.assertEqual(offer.end_epoch, end)
        self.assertEqual(datetime.fromtimestamp(offer.end_epoch, BEIJING).strftime('%Y-%m-%d %H:%M'),
                         '2026-10-12 21:30')
        self.assertIsNone(offer.start_epoch)
        self.assertFalse(confirm_free(self.offer(), free_data('bundle:1', end=end), self.now))

    def test_expired_free_excluded(self):
        self.assertFalse(confirm_free(self.offer(), free_data(end=int(self.now.timestamp()) - 1), self.now))

    def test_free_weekend_permanent_free_news_and_false_flag_rejected(self):
        for text in ('<div class="game_purchase_discount_quantity">Play for free until Monday</div>',
                     '<p>Free to keep when you get it before Monday</p>', free_data(flag=0), free_data(flag='false')):
            with self.subTest(text=text):
                self.assertFalse(confirm_free(self.offer(), text, self.now))

    def test_legacy_deadline_preserved_without_inventing_timezone(self):
        offer = self.offer()
        text = '<div class="game_purchase_discount_quantity">Free to keep when you get it before 12 Oct @ 10:00am.</div>'
        self.assertTrue(confirm_free(offer, text, self.now))
        self.assertIsNone(offer.end_epoch)
        self.assertIn('12 Oct @ 10:00am', offer.deadline_note)

    def test_invalid_timestamp_and_payload_keep_time_unknown(self):
        offer = self.offer()
        self.assertTrue(confirm_free(offer, free_data(end=True), self.now))
        self.assertIsNone(offer.end_epoch)
        self.assertFalse(confirm_free(self.offer(), '<div data-props="{&quot;rgStoreItems&quot;:[]} "></div>', self.now))

    def test_config_validation_and_single_daily_schedule(self):
        config = json.loads(Path('config.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            for key, value in (('page_size', 1.5), ('minimum_discount', True), ('send_gap_seconds', 1), ('country', 'US')):
                path.write_text(json.dumps({**config, key: value}), encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_config(path)
        workflow = Path('.github/workflows/daily.yml').read_text(encoding='utf-8')
        self.assertEqual(workflow.count('cron:'), 1)
        self.assertIn("cron: '30 1 * * *'", workflow)
        self.assertNotIn('*/10', workflow)
        scheduled = datetime(2026, 10, 12, 1, 30, tzinfo=timezone.utc).astimezone(BEIJING)
        self.assertEqual(scheduled.strftime('%H:%M'), '09:30')


if __name__ == '__main__':
    unittest.main()
