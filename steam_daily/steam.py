import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit

from .http import FetchError

SEARCH_URL = 'https://store.steampowered.com/search/results/?'


class ScanChanged(FetchError):
    pass


class Node:
    def __init__(self, tag='', attrs=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.parts = []

    def text(self):
        return ' '.join(' '.join(self.parts).split())

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def with_class(self, class_name):
        return [n for n in self.walk() if class_name in n.attrs.get('class', '').split()]


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {'img', 'br', 'hr', 'input', 'meta', 'link', 'source', 'wbr', 'area', 'base', 'embed', 'param', 'track', 'col'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, text):
        for node in self.stack:
            node.parts.append(text)


@dataclass
class Offer:
    key: str
    name: str
    url: str
    discount: int
    original: str
    current: str
    final_cents: int
    free: bool = False
    end_epoch: int | None = None
    start_epoch: int | None = None
    deadline_note: str = ''
    priced: bool = True
    image_url: str = ''
    review_positive: int | None = None
    review_count: int | None = None

    def to_dict(self):
        return asdict(self)


def safe_image_url(value):
    """Only use observed HTTPS Steam image URLs; never guess CDN asset paths."""
    if not isinstance(value, str):
        return ''
    try:
        parts = urlsplit(value)
        if (parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password
                and parts.port in (None, 443)
                and any(parts.hostname == domain or parts.hostname.endswith('.' + domain)
                        for domain in ('steamstatic.com', 'steamcdn-a.akamaihd.net'))):
            return value
    except ValueError:
        pass
    return ''


def search_metadata(row):
    image_url = next((url for node in row.walk() if node.tag == 'img'
                      if (url := safe_image_url(node.attrs.get('src', '')
                                                or node.attrs.get('data-src', '')))), '')
    reviews = row.with_class('search_review_summary')
    tooltip = Document(reviews[0].attrs.get('data-tooltip-html', '')).root.text() if reviews else ''
    percent = re.search(r'(\d{1,3})%', tooltip)
    count = re.search(r'([\d,]+)\s*篇用户评测', tooltip) or re.search(r'of the ([\d,]+) user reviews', tooltip, re.I)
    positive = int(percent[1]) if percent and int(percent[1]) <= 100 else None
    quantity = int(count[1].replace(',', '')) if count else None
    return {'image_url': image_url, 'review_positive': positive, 'review_count': quantity}


def parse_search(text):
    offers = []
    for row in Document(text).root.with_class('search_result_row'):
        match = re.match(r'https://store\.steampowered\.com/(app|sub|bundle)/(\d+)/', row.attrs.get('href', ''))
        titles = row.with_class('title')
        if not match or not titles or not titles[0].text():
            raise FetchError('Steam search item identity/title changed')
        metadata = search_metadata(row)
        blocks = [n for n in row.walk() if 'data-discount' in n.attrs]
        if not blocks:
            # Specials also contains region-unavailable products with an explicitly empty price block.
            containers = row.with_class('search_price_discount_combined')
            empty_blocks = row.with_class('search_discount_block')
            if (containers and re.fullmatch(r'\d+', containers[0].attrs.get('data-price-final', ''))
                    and not containers[0].text() and empty_blocks and not empty_blocks[0].text()
                    and not row.with_class('discount_final_price')):
                offers.append(Offer(
                    key=f'{match[1]}:{match[2]}', name=titles[0].text(),
                    url=f'https://store.steampowered.com/{match[1]}/{match[2]}/',
                    discount=0, original='未知', current='价格未公开', final_cents=0, priced=False, **metadata,
                ))
                continue
            raise FetchError(f'Steam search discount markup changed for {match[1]}:{match[2]}')
        try:
            discount = int(blocks[0].attrs['data-discount'])
            cents = int(blocks[0].attrs['data-price-final'])
        except (KeyError, ValueError):
            raise FetchError('Steam search price/discount invalid') from None
        if not 0 <= discount <= 100 or cents < 0:
            raise FetchError('Steam search price/discount outside valid range')
        original_nodes = blocks[0].with_class('discount_original_price')
        final_nodes = blocks[0].with_class('discount_final_price')
        if not final_nodes:
            raise FetchError('Steam search formatted price changed')
        offers.append(Offer(
            key=f'{match[1]}:{match[2]}', name=titles[0].text(),
            url=f'https://store.steampowered.com/{match[1]}/{match[2]}/',
            discount=discount, original=original_nodes[0].text() if original_nodes else '未知',
            current=final_nodes[0].text(), final_cents=cents, **metadata,
        ))
    return offers


def numeric_timestamp(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if 946684800 <= value <= 4102444800 else None


def confirm_free(offer, text, now):
    """Use item-scoped purchase data; announcement timestamps are not sale dates."""
    document = Document(text).root
    candidates = []
    for node in document.walk():
        raw = node.attrs.get('data-props', '')
        if 'rgStoreItems' not in raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(data, dict) or not isinstance(data.get('rgPayloads'), list):
            continue
        for payload in data['rgPayloads']:
            if not isinstance(payload, dict) or not isinstance(payload.get('rgStoreItems'), list):
                continue
            for item in payload['rgStoreItems']:
                if not isinstance(item, dict):
                    continue
                kind = {0: 'app', 1: 'sub', 2: 'bundle'}.get(item.get('item_type'))
                if f'{kind}:{item.get("id")}' != offer.key:
                    continue
                options = item.get('purchase_options', [])
                if not isinstance(options, list):
                    options = []
                for option in [item.get('best_purchase_option'), *options]:
                    if isinstance(option, dict) and option.get('is_free_to_keep') in (True, 1) and str(option.get('final_price_in_cents')) == '0':
                        candidates.append(option)
    if candidates:
        ends = [t for option in candidates if (t := numeric_timestamp(option.get('free_to_keep_ends')))]
        offer.end_epoch = min(ends) if ends else None
        offer.free = True
    else:
        # Legacy pages use this dedicated purchase notice; body/news text is not sufficient.
        notices = document.with_class('game_purchase_discount_quantity')
        for notice in notices:
            if re.search(r'Free to keep when you get it before', notice.text(), re.I):
                offer.free = True
                offer.deadline_note = notice.text().split('Some limitations')[0].strip()
                break
    if offer.end_epoch is not None and offer.end_epoch <= int(now.timestamp()):
        return False
    return offer.free


def collect_search(http, config):
    start = 0
    total = None
    offers = {}
    duplicates = 0
    for page in range(config['max_pages']):
        query = {
            'start': start, 'count': config['page_size'], 'specials': 1, 'infinite': 1,
            'cc': config['country'].lower(), 'l': config['language'],
            'sort_by': 'Name_ASC', 'ignore_preferences': 1,
        }
        data = http.json(SEARCH_URL + urlencode(query))
        if not isinstance(data, dict) or data.get('success') != 1:
            raise FetchError('Steam search did not report success')
        if not isinstance(data.get('total_count'), int) or data['total_count'] < 0:
            raise FetchError('Steam search total missing or invalid')
        if data.get('start') != start:
            raise FetchError('Steam returned the wrong search offset')
        if total is None:
            total = data['total_count']
        elif total != data['total_count']:
            raise ScanChanged(f'Steam result count changed during scan ({total} to {data["total_count"]})')
        rows = parse_search(data.get('results_html', ''))
        if total == 0:
            if rows:
                raise FetchError('Steam returned rows for an empty result set')
            return [], {'total': 0, 'pages': 1, 'unpriced': 0}
        if not rows or len(rows) > config['page_size']:
            raise FetchError('Steam pagination incomplete or page size changed')
        for offer in rows:
            if offer.key in offers:
                duplicates += 1
            offers[offer.key] = offer
        start += len(rows)
        print(f'Steam scan: {start}/{total} rows', flush=True)
        if start >= total:
            if start != total or duplicates:
                raise ScanChanged('Steam pagination had duplicate/missing rows')
            return list(offers.values()), {'total': total, 'pages': page + 1,
                                         'unpriced': sum(not item.priced for item in offers.values())}
        time.sleep(config['request_gap_seconds'])
    raise FetchError('Search exceeded max_pages; no incomplete digest will be sent')


def collect(http, config):
    for attempt in range(2):
        try:
            offers, stats = collect_search(http, config)
            break
        except ScanChanged:
            if attempt:
                raise
            print('Steam results changed; restarting the complete scan once.', flush=True)
    now = datetime.now(timezone.utc)
    freebies, discounts, warnings = [], [], []
    if stats['unpriced']:
        warnings.append(f'{stats["unpriced"]} 项商品未公开国区售价，未列入优惠名单。')
    for offer in offers:
        if offer.discount == 100 and offer.final_cents == 0:
            # Fetch English only for the dedicated legacy free-to-keep notice.
            try:
                text = http.request(offer.url + '?' + urlencode({'cc': config['country'].lower(), 'l': 'english'}))
                if confirm_free(offer, text, now):
                    freebies.append(offer)
                else:
                    warnings.append(f'{offer.name}：无法核验为限时永久领取，未列入限免。')
            except FetchError:
                warnings.append(f'{offer.name}：详情页获取失败，未核验限免。')
            time.sleep(config['request_gap_seconds'])
        elif config['minimum_discount'] <= offer.discount < 100 and offer.final_cents > 0:
            discounts.append(offer)
    freebies.sort(key=lambda item: (item.end_epoch or 4102444801, item.key))
    discounts.sort(key=lambda item: (-item.discount, item.final_cents, item.key))
    return freebies, discounts, stats, warnings
