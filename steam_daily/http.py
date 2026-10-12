import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class FetchError(RuntimeError):
    pass


def retry_delay(error, attempt):
    fallback = 60 if error.code == 429 else 2 ** (attempt + 1)
    value = error.headers.get('Retry-After', '') if error.headers else ''
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return fallback
    if seconds > 60:
        # Do not retry earlier than the provider asks; leave longer pauses to a later run.
        raise FetchError('Server asked for a long retry delay; rerun later')
    return max(1, seconds)


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if new.scheme != 'https' and new.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise FetchError('Insecure redirect refused')
        if req.data is not None:
            raise FetchError('POST redirect refused; response unknown')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and (old.scheme, old.hostname, old.port) != (new.scheme, new.hostname, new.port):
            for name in list(redirected.headers):
                if name.lower() in {'authorization', 'proxy-authorization', 'cookie', 'access-key', 'x-api-key'}:
                    redirected.remove_header(name)
        return redirected


class Http:
    """GET retries are safe; message POSTs deliberately have no automatic retries."""

    def __init__(self, timeout=30):
        self.timeout = timeout
        self.opener = build_opener(SafeRedirect())

    def request(self, url, payload=None, headers=None, *, retry=True, binary=False):
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        request_headers = {'User-Agent': 'Steam-WeChat-Daily/1.0', **(headers or {})}
        if data is not None:
            request_headers['Content-Type'] = 'application/json'
        attempts = 3 if retry and data is None else 1
        for attempt in range(attempts):
            delay = 2 ** (attempt + 1)
            try:
                request = Request(url, data=data, headers=request_headers)
                with self.opener.open(request, timeout=self.timeout) as response:
                    body = response.read()
                return body if binary else body.decode('utf-8')
            except HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                    raise FetchError(f'HTTP {error.code}; request failed') from None
                delay = retry_delay(error, attempt)
                print(f'HTTP {error.code}: waiting {delay:g}s before GET retry.', flush=True)
            except (URLError, TimeoutError, OSError, HTTPException, UnicodeError):
                if attempt == attempts - 1:
                    raise FetchError('Network error or timeout; response unknown') from None
            time.sleep(delay)

    def json(self, url, payload=None, headers=None, *, retry=True):
        try:
            return json.loads(self.request(url, payload, headers, retry=retry))
        except (ValueError, UnicodeError):
            raise FetchError('Response was not valid JSON') from None
