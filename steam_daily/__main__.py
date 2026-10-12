import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .delivery import PushPlus, deliver, restore_state, save_json, validate_state
from .http import FetchError, Http
from .report import BEIJING, build_pages
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


def run(args):
    config = load_config(args.config)
    token = os.environ.get('PUSHPLUS_TOKEN', '').strip()
    if args.send and not token:
        raise ValueError('Add PUSHPLUS_TOKEN to GitHub Actions Secrets before sending')
    http = Http()
    report_dir = Path(args.report_dir)
    state_path = Path(args.state)
    now = datetime.now(BEIJING)
    day = now.strftime('%Y-%m-%d')
    if args.send and args.restore:
        restore_state(http, state_path)
    state = None
    if args.send and state_path.exists():
        old = json.loads(state_path.read_text(encoding='utf-8'))
        validate_state(old)
        if old.get('day') == day:
            state = old
            print('Resuming the saved daily digest; accepted pages will not be resent.', flush=True)
    if state is None:
        freebies, discounts, stats, warnings = collect(http, config)
        now = datetime.now(BEIJING)
        day = now.strftime('%Y-%m-%d')
        print(f'Scan complete: {stats["total"]} rows; {len(freebies)} free, {len(discounts)} discounted.', flush=True)
        bodies = build_pages(freebies, discounts, now, config, stats, warnings)
        report_dir.mkdir(parents=True, exist_ok=True)
        snapshot = {'captured_at': now.isoformat(), 'country': config['country'], 'stats': stats,
                    'freebies': [item.to_dict() for item in freebies],
                    'discounts': [item.to_dict() for item in discounts], 'warnings': warnings}
        save_json(report_dir / 'offers.json', snapshot)
        for index, body in enumerate(bodies, 1):
            (report_dir / f'page-{index:03}.html').write_text(
                '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Steam优惠晨报</title>' + body + '</html>',
                encoding='utf-8',
            )
        expires = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=BEIJING)
        state = {
            'schema': 1, 'day': day, 'captured_at': now.isoformat(),
            'expires_ms': int(expires.timestamp() * 1000),
            'pages': [{'title': f'Steam优惠 {day} [{i}/{len(bodies)}]', 'content': body,
                       'status': 'ready', 'attempts': 0} for i, body in enumerate(bodies, 1)],
        }
        if args.send:
            save_json(state_path, state)
    if not args.send:
        print(f'Preview only: {len(state["pages"])} pages written to {report_dir}; no messages sent.', flush=True)
        return
    client = PushPlus(http, token, os.environ.get('PUSHPLUS_SECRET_KEY', '').strip(), gap=config['send_gap_seconds'])
    deliver(state, state_path, client)
    verified = all(page['status'] == 'delivered' for page in state['pages'])
    print('Final delivery verified.' if verified else 'PushPlus accepted all pages; actual WeChat delivery not verified.', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Daily Steam free-to-keep and >=50% discount digest')
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--report-dir', default='reports')
    parser.add_argument('--state', default='state/state.json')
    parser.add_argument('--send', action='store_true', help='Send through PushPlus; default is local preview')
    parser.add_argument('--restore', action='store_true', help='Restore the latest journal from GitHub Actions artifacts')
    args = parser.parse_args()
    try:
        run(args)
    except (FetchError, ValueError, KeyError, OSError) as error:
        # Only controlled error types are logged; never dump request headers/bodies.
        message = str(error)
        for name in ('PUSHPLUS_TOKEN', 'PUSHPLUS_SECRET_KEY', 'GITHUB_TOKEN'):
            secret = os.environ.get(name, '')
            if secret:
                message = message.replace(secret, '[redacted]')
        print(f'ERROR: {message}', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
