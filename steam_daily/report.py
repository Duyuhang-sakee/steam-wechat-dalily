import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .steam import Offer, safe_image_url

BEIJING = timezone(timedelta(hours=8), 'Asia/Shanghai')
MODE = 'single-digest-v2'


def display_time(epoch):
    return datetime.fromtimestamp(epoch, BEIJING).strftime('%Y-%m-%d %H:%M') if epoch else '未知'


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


def row(offer, now):
    due = ''
    if offer.free and offer.end_epoch and 0 < offer.end_epoch - now.timestamp() <= 86400:
        due = ' <strong>【未来24小时内结束】</strong>'
    name = offer.name if len(offer.name) <= 90 else offer.name[:89] + '…'
    price = f'{html.escape(offer.original)} → {html.escape(offer.current)} · -{offer.discount}%'
    detail = ''
    if offer.free:
        detail = f'<br>领取截止：{display_time(offer.end_epoch)}'
        if offer.deadline_note and not offer.end_epoch:
            detail += f'；商店原文：{html.escape(offer.deadline_note[:200])}（时区未核实）'
    return (f'<p><a href="{html.escape(offer.url, quote=True)}">{html.escape(name)}</a>{due}<br>'
            f'{price}{detail}</p>')


def build_digest(freebies, discounts, now, config, url, warnings):
    day = now.astimezone(BEIJING).strftime('%Y-%m-%d')
    link = site_url(url) + '?day=' + day if url else ''
    header = (f'<h2>Steam 优惠晨报 · {day}</h2><p>国区 · 限免 {len(freebies)} 项 · '
              f'折扣≥{config["minimum_discount"]}% 共 {len(discounts)} 项<br>'
              f'采集于 {now.astimezone(BEIJING):%H:%M}（北京时间）</p>')
    footer = (f'<hr><p><a href="{html.escape(link, quote=True)}">查看全部优惠与游戏封面 →</a></p>' if link
              else '<hr><p>本地预览：完整名单见 site/index.html；配置 Pages 后这里显示网页链接。</p>')
    footer += '<p>每日一条摘要；价格及领取状态以 Steam 商店为准。精选可能包含 DLC、软件或礼包。</p>'
    if warnings:
        footer += '<p>部分条目未能核验，详情见完整网页。</p>'
    blocks = ['<h3>限时永久领取</h3>']
    remaining = config['message_char_limit'] - len(header) - len(footer)
    free_rows = [row(item, now) for item in freebies]
    recommendations = select_recommendations(discounts)
    featured = '<h3>折扣精选</h3>'
    for item in recommendations:
        block = row(item, now)
        if len(featured + block) > remaining // 2:
            break
        featured += block
    if not recommendations:
        featured += '<p>暂无符合条件的商品。</p>'
    for block in free_rows:
        if sum(map(len, blocks)) + len(block) + len(featured) + 70 > remaining:
            break
        blocks.append(block)
    shown = len(blocks) - 1
    if shown < len(free_rows):
        blocks.append(f'<p>另有 {len(free_rows) - shown} 项限免，请查看完整网页。</p>')
    if not free_rows:
        blocks.append('<p>本次未核验到有效限免。</p>')
    content = header + ''.join(blocks) + featured + footer
    if len(content) > config['message_char_limit']:
        raise ValueError('Daily summary exceeded message length budget')
    return content


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
