import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from .delivery import Bark, PushPlus, deliver, deliver_bark, refresh_notification, restore_state, save_json, validate_state
from .http import FetchError, Http
from .report import BEIJING, MODE, build_page_notification, build_site, site_url, snapshot_hash, unpack_snapshot
from .steam import collect


def load_config(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Expected a config object')
    for key, minimum, maximum in (
        ('minimum_discount', 1, 99), ('page_size', 1, 100), ('max_pages', 1, 500),
        ('request_gap_seconds', 0, 10),
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
    (directory / 'wechat-notification.html').write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1"><title>微信页面入口预览</title>' + content + '</html>',
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
    bark_key = os.environ.get('BARK_DEVICE_KEY', '').strip()
    if state['suppressed']:
        print('Legacy messages were already attempted today. Remaining pages stopped; new page notification starts tomorrow.', flush=True)
        if not bark_key:
            return
    deployed = site_url(args.deployed_url)
    if deployed != state['site_url']:
        raise ValueError('Deployed Pages URL does not match the prepared page notification')
    token = os.environ.get('PUSHPLUS_TOKEN', '').strip()
    if not token and not bark_key:
        raise ValueError('Add PUSHPLUS_TOKEN or BARK_DEVICE_KEY to GitHub Actions Secrets before sending')
    # A successful Pages deployment alone may precede CDN propagation. Poll the public
    # version briefly instead of failing the send on the first stale response.
    published = False
    for attempt in range(5):
        try:
            manifest = http.json(deployed + 'manifest.json?digest=' + state['snapshot_sha256'])
        except FetchError:
            manifest = None
        if (isinstance(manifest, dict) and manifest.get('schema') == 1
                and manifest.get('snapshot_sha256') == state['snapshot_sha256']
                and manifest.get('captured_at') == state['captured_at']):
            published = True
            break
        if attempt < 4:
            print(f'Deployed snapshot not visible yet; waiting 20s before check {attempt + 2} of 5.', flush=True)
            time.sleep(20)
    if not published:
        raise FetchError('Published website is not the prepared snapshot; no message sent')
    errors = []
    if token and not state['suppressed']:
        try:
            client = PushPlus(http, token, os.environ.get('PUSHPLUS_SECRET_KEY', '').strip(), gap=config['send_gap_seconds'])
            page = state['pages'][0]
            previous_attempts = page['attempts']
            deliver(state, path, client)
            verified = page['status'] == 'delivered'
            if page['attempts'] > previous_attempts:
                result = ('final WeChat delivery verified.' if verified
                          else 'PushPlus accepted it, but WeChat delivery is not verified.')
                print('Submitted ONE new page notification in this run; ' + result, flush=True)
            elif verified:
                print("No new notification submitted: today's previous notification is verified delivered; duplicate send skipped.", flush=True)
            else:
                print("No new notification submitted: PushPlus already accepted today's notification; duplicate send skipped. "
                      "Final WeChat delivery remains unverified; check PushPlus message history.", flush=True)
        except FetchError as error:
            errors.append(f'WeChat: {error}')
    if bark_key:
        try:
            bark = Bark(http, bark_key, os.environ.get('BARK_SERVER_URL', '').strip() or 'https://api.day.app')
            deliver_bark(state, path, bark)
        except (FetchError, ValueError) as error:
            errors.append(f'Bark: {error}')
    if errors:
        raise FetchError('; '.join(errors))


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
        notification = build_page_notification(datetime.fromisoformat(snapshot['captured_at']), url)
        if refresh_notification(old):
            save_json(state_path, old)
        write_reports(args.report_dir, snapshot, config, notification)
        print(f'Reused today\'s saved snapshot (captured at {old["captured_at"]}); '
              'no Steam rescan in this run. Rebuilt report files from saved data.', flush=True)
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
    content = build_page_notification(now, url)
    write_reports(args.report_dir, snapshot, config, content)
    print(f'Report complete: {len(freebies)} free, {len(discounts)} discounted; ONE page notification per configured channel.', flush=True)
    if not args.prepare_send:
        print(f'Preview only: webpage and page notification in {args.report_dir}; no deployment or messages.', flush=True)
        return
    day = now.strftime('%Y-%m-%d')
    expires = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=BEIJING)
    # Any legacy attempt may have reached the provider; suppress another notification that day.
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
        print('Legacy sends detected: website prepared, but no additional WeChat page notification today.', flush=True)
    validate_state(state)
    save_json(snapshot_path, snapshot)
    save_json(state_path, state)


def main():
    parser = argparse.ArgumentParser(description='Daily WeChat/Bark page notifications and a complete Steam cover gallery')
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--report-dir', default='reports')
    parser.add_argument('--state', default='state/state.json')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare-send', action='store_true', help='Restore/scan and prepare one journal before deploying')
    mode.add_argument('--send', action='store_true', help='Send the prepared page notification after verifying the deployed website')
    parser.add_argument('--restore', action='store_true', help='Restore the latest journal and bound snapshot from Actions')
    parser.add_argument('--site-url', default='', help='Actual Pages HTTPS base URL for the page notification')
    parser.add_argument('--deployed-url', default='', help='Successful deploy-pages page_url; required with --send')
    parser.add_argument('--snapshot', default='', help='Reuse offers.json for local preview without a Steam scan')
    args = parser.parse_args()
    try:
        if args.restore and not args.prepare_send:
            raise ValueError('--restore must be used with --prepare-send')
        run(args)
    except (FetchError, ValueError, KeyError, TypeError, OSError) as error:
        message = str(error)
        for name in ('PUSHPLUS_TOKEN', 'PUSHPLUS_SECRET_KEY', 'BARK_DEVICE_KEY', 'GITHUB_TOKEN'):
            secret = os.environ.get(name, '')
            if secret:
                message = message.replace(secret, '[redacted]')
        print(f'ERROR: {message}', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
