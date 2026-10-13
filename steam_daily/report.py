import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .steam import Offer, safe_image_url

BEIJING = timezone(timedelta(hours=8), 'Asia/Shanghai')
# Preserve the stored mode so existing send receipts still prevent duplicate notifications.
MODE = 'single-digest-v2'


def site_url(value):
    try:
        parts = urlsplit(value)
        valid = (parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password
                 and parts.port in (None, 443) and not parts.query and not parts.fragment
                 and not re.search(r'[\s<>"\\]', value) and len(value) <= 500)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Provide an HTTPS Pages URL without credentials, query or fragment')
    return value.rstrip('/') + '/'


def snapshot_hash(snapshot):
    data = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def unpack_snapshot(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('country') != 'CN':
        raise ValueError('Invalid report snapshot')
    now = datetime.fromisoformat(snapshot['captured_at'])
    if now.tzinfo is None:
        raise ValueError('Snapshot capture time requires a timezone')
    groups = []
    seen = set()
    fields = Offer.__dataclass_fields__
    for group in ('freebies', 'discounts'):
        values = snapshot.get(group)
        if not isinstance(values, list):
            raise ValueError('Invalid snapshot offers')
        offers = []
        for value in values:
            if not isinstance(value, dict):
                raise ValueError('Invalid snapshot item')
            offer = Offer(**{key: item for key, item in value.items() if key in fields})
            match = re.fullmatch(r'(app|sub|bundle):(\d+)', offer.key)
            if (not match or offer.key in seen or offer.url != f'https://store.steampowered.com/{match[1]}/{match[2]}/'
                    or not isinstance(offer.name, str) or not offer.name
                    or not isinstance(offer.original, str) or not isinstance(offer.current, str)
                    or type(offer.discount) is not int or not 0 <= offer.discount <= 100
                    or type(offer.final_cents) is not int or offer.final_cents < 0
                    or type(offer.free) is not bool or offer.free != (group == 'freebies')):
                raise ValueError('Invalid snapshot identity or price')
            for epoch in (offer.end_epoch, offer.start_epoch):
                if epoch is not None and (type(epoch) is not int or not 946684800 <= epoch <= 4102444800):
                    raise ValueError('Invalid snapshot promotion time')
            if not isinstance(offer.deadline_note, str):
                raise ValueError('Invalid snapshot deadline note')
            if offer.review_positive is not None and (type(offer.review_positive) is not int or not 0 <= offer.review_positive <= 100):
                raise ValueError('Invalid snapshot review score')
            if offer.review_count is not None and (type(offer.review_count) is not int or offer.review_count < 0):
                raise ValueError('Invalid snapshot review count')
            offer.image_url = safe_image_url(offer.image_url)
            seen.add(offer.key)
            offers.append(offer)
        groups.append(offers)
    if not isinstance(snapshot.get('warnings'), list) or any(not isinstance(w, str) for w in snapshot['warnings']):
        raise ValueError('Invalid snapshot warnings')
    return *groups, now.astimezone(BEIJING)


def select_recommendations(discounts, count=5):
    popular = [item for item in discounts if item.review_positive is not None
               and item.review_positive >= 80 and (item.review_count or 0) >= 500]
    popular.sort(key=lambda item: (-(item.review_count or 0), -item.discount, item.final_cents, item.key))
    selected = popular[:count]
    keys = {item.key for item in selected}
    fallback = sorted(discounts, key=lambda item: (-item.discount, item.final_cents, item.key))
    selected.extend(item for item in fallback if item.key not in keys)
    return selected[:count]


def build_page_notification(now, url=''):
    day = now.astimezone(BEIJING).strftime('%Y-%m-%d')
    link = (site_url(url) if url else 'site/index.html') + '?day=' + day
    return (f'<p><a href="{html.escape(link, quote=True)}">'
            f'Steam 优惠晨报 · {day} · 打开完整优惠页面 →</a></p>')


def build_site(snapshot, config):
    freebies, discounts, now = unpack_snapshot(snapshot)
    metadata = {'captured_at': now.isoformat(), 'day': now.strftime('%Y-%m-%d'),
                'minimum_discount': snapshot.get('minimum_discount', config['minimum_discount']),
                'country': 'CN', 'stats': snapshot['stats'], 'warnings': snapshot['warnings'],
                'recommended': [item.key for item in select_recommendations(discounts)]}
    data = {**metadata, 'offers': [item.to_dict() for item in freebies + discounts]}
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    for character, replacement in (('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'),
                                    ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        payload = payload.replace(character, replacement)
    directory = Path(__file__).parent / 'templates'
    template = (directory / 'site.html').read_text(encoding='utf-8')
    return (template.replace('@@STYLE@@', (directory / 'site.css').read_text(encoding='utf-8'))
            .replace('@@SCRIPT@@', (directory / 'site.js').read_text(encoding='utf-8'))
            .replace('@@DATA@@', payload))
