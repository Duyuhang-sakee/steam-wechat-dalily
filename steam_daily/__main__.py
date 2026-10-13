import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .delivery import PushPlus, deliver, restore_state, save_json, validate_state
from .http import FetchError, Http
from .report import BEIJING, MODE, build_digest, build_site, site_url, snapshot_hash, unpack_snapshot
from .steam import collect


def load_config(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Expected a config object')
    for key, minimum, maximum in (
        ('minimum_discount', 1, 99), ('page_size', 1, 100), ('max_pages', 1, 500),
        ('message_char_limit', 2000, 18000), ('request_gap_seconds', 0, 10),
        ('send_gap_seconds', 13, 60),
    ):
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
            raise ValueError(f'Invalid config: {key}')
        if key not in ('request_gap_seconds', 'send_gap_seconds') and not isinstance(value, int):
            raise ValueError(f'Expected integer config: {key}')
    if data.get('country') != 'CN' or data.get('language') not in ('schinese', 'english'):
        raise ValueError('This version supports CN pricing and schinese/english language')
    return data


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_reports(directory, snapshot, config, content):
    directory = Path(directory)
    website = directory / 'site'
    website.mkdir(parents=True, exist_ok=True)
    save_json(directory / 'offers.json', snapshot)
    save_json(website / 'offers.json', snapshot)
    save_json(website / 'manifest.json', {
        'schema': 1, 'captured_at': snapshot['captured_at'], 'snapshot_sha256': snapshot_hash(snapshot),
    })
    (website / 'index.html').write_text(build_site(snapshot, config), encoding='utf-8')
    (directory / 'wechat-summary.html').write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1"><title>微信摘要预览</title>' + content + '</html>',
        encoding='utf-8',
    )


def send_prepared(args, config, http):
    path = Path(args.state)
    state = read_json(path)
    validate_state(state)
    if state.get('mode') != MODE:
        raise ValueError('Legacy paginated journal cannot be sent; prepare the new digest first')
    now = datetime.now(BEIJING)
    if state['day'] != now.strftime('%Y-%m-%d') or now.timestamp() * 1000 >= state['expires_ms']:
        raise ValueError('Prepared digest expired; prepare and deploy a fresh snapshot')
    if state['suppressed']:
        print('Legacy messages were already attempted today. Remaining pages stopped; new summary starts tomorrow.', flush=True)
        return
    deployed = site_url(args.deployed_url)
    if deployed != state['site_url']:
        raise ValueError('Deployed Pages URL does not match the prepared summary')
    token = os.environ.get('PUSHPLUS_TOKEN', '').strip()
    if not token:
        raise ValueError('Add PUSHPLUS_TOKEN to GitHub Actions Secrets before sending')
    # A successful Pages deployment alone may precede CDN propagation. Check the public version too.
    manifest = http.json(deployed + 'manifest.json?digest=' + state['snapshot_sha256'])
    if (not isinstance(manifest, dict) or manifest.get('schema') != 1
            or manifest.get('snapshot_sha256') != state['snapshot_sha256']
            or manifest.get('captured_at') != state['captured_at']):
        raise FetchError('Published website is not the prepared snapshot; no message sent')
    client = PushPlus(http, token, os.environ.get('PUSHPLUS_SECRET_KEY', '').strip(), gap=config['send_gap_seconds'])
    deliver(state, path, client)
    verified = state['pages'][0]['status'] == 'delivered'
    print('Final delivery verified.' if verified else 'PushPlus accepted the single summary; WeChat delivery not verified.', flush=True)


def run(args):
    config = load_config(args.config)
    http = Http()
    if args.send:
        send_prepared(args, config, http)
        return
    url = site_url(args.site_url) if args.site_url else ''
    if args.prepare_send and not url:
        raise ValueError('Preparing a send requires the actual Pages site URL')
    state_path = Path(args.state)
    snapshot_path = state_path.parent / 'offers.json'
    now = datetime.now(BEIJING)
    day = now.strftime('%Y-%m-%d')
    if args.prepare_send and args.restore:
        restore_state(http, state_path, snapshot_path)
    old = None
    if args.prepare_send and state_path.exists():
        old = read_json(state_path)
        validate_state(old)
        if old['day'] != day:
            old = None
    if old and old.get('mode') == MODE:
        if old['site_url'] != url:
            raise ValueError('Pages URL changed during the day; review the prepared state before sending')
        snapshot = read_json(snapshot_path)
        unpack_snapshot(snapshot)
        if snapshot_hash(snapshot) != old['snapshot_sha256'] or snapshot['captured_at'] != old['captured_at']:
            raise ValueError('Saved snapshot does not match the sending journal')
        write_reports(args.report_dir, snapshot, config, old['pages'][0]['content'])
        print('Restored the same daily snapshot and single-message journal.', flush=True)
        return
    if args.snapshot:
        if args.prepare_send:
            raise ValueError('--snapshot is for local preview only')
        snapshot = read_json(args.snapshot)
        freebies, discounts, now = unpack_snapshot(snapshot)
        config = {**config, 'minimum_discount': snapshot.get('minimum_discount', config['minimum_discount'])}
    else:
        freebies, discounts, stats, warnings = collect(http, config)
        now = datetime.now(BEIJING)
        snapshot = {
            'captured_at': now.isoformat(), 'country': config['country'],
            'minimum_discount': config['minimum_discount'], 'stats': stats,
            'freebies': [item.to_dict() for item in freebies],
            'discounts': [item.to_dict() for item in discounts], 'warnings': warnings,
        }
    content = build_digest(freebies, discounts, now, config, url, snapshot['warnings'])
    write_reports(args.report_dir, snapshot, config, content)
    print(f'Report complete: {len(freebies)} free, {len(discounts)} discounted; ONE WeChat summary.', flush=True)
    if not args.prepare_send:
        print(f'Preview only: webpage and summary in {args.report_dir}; no deployment or messages.', flush=True)
        return
    day = now.strftime('%Y-%m-%d')
    expires = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=BEIJING)
    # Any legacy attempt may have reached the provider. Stop its remaining pages for this day.
    suppressed = bool(old and old['day'] == day and any(
        page['status'] != 'ready' or page['attempts'] > 0 or page.get('receipt') for page in old['pages']))
    state = {
        'schema': 1, 'mode': MODE, 'day': day, 'captured_at': now.isoformat(),
        'expires_ms': int(expires.timestamp() * 1000), 'site_url': url,
        'snapshot_sha256': snapshot_hash(snapshot), 'suppressed': suppressed,
        'pages': [{'title': f'Steam优惠晨报 {day}', 'content': content, 'status': 'ready', 'attempts': 0}],
    }
    if suppressed:
        state['legacy_sends'] = [{key: page[key] for key in ('title', 'status', 'attempts', 'receipt') if key in page}
                                 for page in old['pages']]
        print('Legacy sends detected: website prepared, but no additional WeChat summary today.', flush=True)
    validate_state(state)
    save_json(snapshot_path, snapshot)
    save_json(state_path, state)


def main():
    parser = argparse.ArgumentParser(description='One daily WeChat summary and a complete Steam cover gallery')
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--report-dir', default='reports')
    parser.add_argument('--state', default='state/state.json')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare-send', action='store_true', help='Restore/scan and prepare one journal before deploying')
    mode.add_argument('--send', action='store_true', help='Send the prepared summary after verifying the deployed website')
    parser.add_argument('--restore', action='store_true', help='Restore the latest journal and bound snapshot from Actions')
    parser.add_argument('--site-url', default='', help='Actual Pages HTTPS base URL for the summary')
    parser.add_argument('--deployed-url', default='', help='Successful deploy-pages page_url; required with --send')
    parser.add_argument('--snapshot', default='', help='Reuse offers.json for local preview without a Steam scan')
    args = parser.parse_args()
    try:
        if args.restore and not args.prepare_send:
            raise ValueError('--restore must be used with --prepare-send')
        run(args)
    except (FetchError, ValueError, KeyError, TypeError, OSError) as error:
        message = str(error)
        for name in ('PUSHPLUS_TOKEN', 'PUSHPLUS_SECRET_KEY', 'GITHUB_TOKEN'):
            secret = os.environ.get(name, '')
            if secret:
                message = message.replace(secret, '[redacted]')
        print(f'ERROR: {message}', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
