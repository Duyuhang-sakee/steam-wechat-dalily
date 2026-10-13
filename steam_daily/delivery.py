import io
import json
import os
import re
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from .http import FetchError
from .report import BEIJING, MODE, build_page_notification, site_url, snapshot_hash, unpack_snapshot


class SendRejected(FetchError):
    pass


def validate_state(state):
    if not isinstance(state, dict) or type(state.get('schema')) is not int or state['schema'] != 1:
        raise ValueError('Unsupported sending journal schema')
    try:
        day = datetime.strptime(state['day'], '%Y-%m-%d').date()
        captured = datetime.fromisoformat(state['captured_at'])
    except (KeyError, TypeError, ValueError):
        raise ValueError('Invalid sending journal dates') from None
    expires = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=BEIJING)
    if (captured.tzinfo is None or captured.astimezone(BEIJING).date() != day
            or type(state.get('expires_ms')) is not int
            or state['expires_ms'] != int(expires.timestamp() * 1000)):
        raise ValueError('Invalid sending journal expiry')
    pages = state.get('pages')
    if not isinstance(pages, list) or not pages or len(pages) > 500:
        raise ValueError('Invalid sending journal pages')
    statuses = {'ready', 'sending', 'accepted', 'pending', 'delivered', 'failed', 'uncertain'}
    for page in pages:
        if (not isinstance(page, dict) or not isinstance(page.get('title'), str)
                or not 1 <= len(page['title']) <= 100 or not isinstance(page.get('content'), str)
                or not 1 <= len(page['content']) <= 18000 or not isinstance(page.get('status'), str)
                or page['status'] not in statuses
                or type(page.get('attempts')) is not int or not 0 <= page['attempts'] <= 3):
            raise ValueError('Invalid sending journal page')
        if page['status'] in ('accepted', 'pending', 'delivered') and (
                not isinstance(page.get('receipt'), str) or not 1 <= len(page['receipt']) <= 1000):
            raise ValueError('Sending journal is missing a receipt')
    if len(json.dumps(state, ensure_ascii=False).encode('utf-8')) > 4_500_000:
        raise ValueError('Sending journal exceeds artifact size budget')
    if 'mode' in state:
        if (state['mode'] != MODE or len(pages) != 1
                or not isinstance(state.get('site_url'), str)
                or site_url(state['site_url']) != state['site_url']
                or not isinstance(state.get('snapshot_sha256'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', state['snapshot_sha256'])
                or type(state.get('suppressed')) is not bool):
            raise ValueError('Invalid single-message journal')


def refresh_notification(state):
    if state.get('mode') != MODE:
        return False
    page = state['pages'][0]
    replaceable = page['status'] == 'failed' or (
        page['status'] == 'ready' and page['attempts'] == 0 and not page.get('receipt'))
    if not replaceable:
        return False
    content = build_page_notification(datetime.fromisoformat(state['captured_at']), state['site_url'])
    if page['content'] == content:
        return False
    page['content'] = content
    return True


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def restore_state(http, path, snapshot_path=None):
    """Restore the latest saved journal and its bound snapshot."""
    repository = os.environ.get('GITHUB_REPOSITORY')
    token = os.environ.get('GITHUB_TOKEN')
    if not repository or not token:
        return
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid GITHUB_REPOSITORY')
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28'}
    base = f'https://api.github.com/repos/{repository}/actions/artifacts'
    data = http.json(base + '?per_page=100', headers=headers)
    if not isinstance(data, dict) or not isinstance(data.get('artifacts'), list):
        raise FetchError('GitHub artifact list invalid')
    current_name = f'steam-daily-state-{os.environ.get("GITHUB_RUN_ID")}-{os.environ.get("GITHUB_RUN_ATTEMPT", "1")}'
    candidates = [a for a in data.get('artifacts', [])
                  if isinstance(a, dict) and type(a.get('id')) is int
                  and isinstance(a.get('name'), str) and re.fullmatch(r'steam-daily-state-\d+-\d+', a['name'])
                  and not a.get('expired') and a['name'] != current_name]
    if not candidates:
        if data.get('total_count', 0) > len(data['artifacts']):
            raise FetchError('No journal found among recent artifacts; inspect before sending')
        return
    artifact = max(candidates, key=lambda item: item['id'])
    content = http.request(base + f'/{artifact["id"]}/zip', headers=headers, binary=True)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            member = archive.getinfo('state.json')
            if member.file_size > 5_000_000:
                raise ValueError('State artifact unexpectedly large')
            state = json.loads(archive.read(member))
            validate_state(state)
            snapshot = None
            if state.get('mode') == MODE and snapshot_path is not None:
                snapshot_member = archive.getinfo('offers.json')
                if snapshot_member.file_size > 20_000_000:
                    raise ValueError('Report snapshot unexpectedly large')
                snapshot = json.loads(archive.read(snapshot_member))
                unpack_snapshot(snapshot)
                if snapshot_hash(snapshot) != state['snapshot_sha256']:
                    raise ValueError('Restored report does not match sending journal')
    except (zipfile.BadZipFile, KeyError):
        raise ValueError('Invalid state artifact archive') from None
    validate_state(state)
    if snapshot is not None:
        save_json(snapshot_path, snapshot)
    save_json(path, state)


class PushPlus:
    def __init__(self, http, token, secret_key='', *, gap=13):
        self.http = http
        self.token = token
        self.secret_key = secret_key
        self.access_key = None
        self.gap = gap
        self.last_request = None

    def _request(self, url, payload=None, headers=None):
        # All provider requests share the same pacing, including authentication/status queries.
        if self.last_request is not None:
            time.sleep(max(0, self.gap - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        return self.http.json(url, payload, headers, retry=False)

    def send(self, title, content, deadline):
        response = self._request('https://www.pushplus.plus/send', {
            'token': self.token, 'title': title, 'content': content,
            'channel': 'wechat', 'template': 'html', 'timestamp': deadline,
        })
        if not isinstance(response, dict):
            raise FetchError('PushPlus response invalid; result unknown')
        if response.get('code') != 200:
            # Do not log provider-controlled messages that may echo request credentials.
            code = response.get('code')
            code = code if type(code) is int else 'unknown'
            if type(code) is int:
                raise SendRejected(f'PushPlus rejected request (code {code})')
            raise FetchError('PushPlus response code invalid; result unknown')
        if (not isinstance(response.get('data'), str) or not 1 <= len(response['data']) <= 1000
                or any(secret and secret in response['data'] for secret in (self.token, self.secret_key, self.access_key))):
            raise FetchError('PushPlus accepted request without a usable receipt')
        return response['data']

    def status(self, receipt):
        if not self.secret_key:
            return None
        if not self.access_key:
            response = self._request('https://www.pushplus.plus/api/common/openApi/getAccessKey', {
                'token': self.token, 'secretKey': self.secret_key,
            })
            if not isinstance(response, dict) or response.get('code') != 200:
                raise FetchError('PushPlus Open API authentication failed; verify key and IP allowlist')
            data = response.get('data')
            if not isinstance(data, dict) or not isinstance(data.get('accessKey'), str) or not data['accessKey']:
                raise FetchError('PushPlus Open API access key invalid')
            self.access_key = data['accessKey']
        response = self._request('https://www.pushplus.plus/api/open/message/sendMessageResult?' +
                                  urlencode({'shortCode': receipt}), headers={'access-key': self.access_key})
        if not isinstance(response, dict) or response.get('code') != 200 or not isinstance(response.get('data'), dict):
            raise FetchError('PushPlus delivery status query failed')
        status = response.get('data', {}).get('status')
        if type(status) is not int or status not in (0, 1, 2, 3):
            raise FetchError('PushPlus delivery status invalid')
        return status


def deliver(state, path, client, *, poll_seconds=5):
    """Persist intent before each POST. Ambiguous responses require manual reconciliation."""
    validate_state(state)
    for index, page in enumerate(state['pages'], 1):
        status = page['status']
        if status == 'delivered':
            continue
        if status in ('sending', 'uncertain'):
            raise FetchError(f'Page {index} has an uncertain previous send; inspect before resetting state')
        if status in ('accepted', 'pending'):
            final = client.status(page['receipt'])
            if final is None:
                continue  # Token-only mode confirms acceptance, not delivery.
            if final == 2:
                page['status'] = 'delivered'
                save_json(path, state)
                continue
            if final != 3:
                raise FetchError(f'Page {index} still awaiting final delivery; rerun later')
            page['status'] = 'failed'
        if page.get('attempts', 0) >= 3:
            raise FetchError(f'Page {index} exhausted its confirmed-failure retries')
        if datetime.now(BEIJING).timestamp() * 1000 >= state['expires_ms']:
            raise FetchError('Daily digest expired; generate a fresh daily snapshot')
        refresh_notification(state)
        page['status'] = 'sending'
        page['attempts'] = page.get('attempts', 0) + 1
        save_json(path, state)
        try:
            receipt = client.send(page['title'], page['content'], state['expires_ms'])
        except SendRejected:
            page['status'] = 'failed'
            save_json(path, state)
            raise
        except FetchError:
            # Timeouts and invalid responses need review rather than a blind resend.
            page['status'] = 'uncertain'
            save_json(path, state)
            raise
        page.update(status='accepted', receipt=receipt)
        save_json(path, state)
        final = None
        if client.secret_key:
            for _ in range(6):
                time.sleep(poll_seconds)
                final = client.status(receipt)
                if final in (2, 3):
                    break
            page['status'] = {2: 'delivered', 3: 'failed'}.get(final, 'pending')
            save_json(path, state)
            if final == 3:
                raise FetchError(f'Page {index} failed final delivery; rerun to retry')
        print(f'Page {index}/{len(state["pages"])}: {page["status"]}', flush=True)
    if any(p['status'] in ('pending', 'failed', 'uncertain', 'sending') for p in state['pages']):
        raise FetchError('One or more message pages are not confirmed; inspect state artifact')
