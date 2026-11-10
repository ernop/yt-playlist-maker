"""Every request to an outside service goes through here (SPEC.md, "Outside services"): its User-Agent, at most
one request per interval to each service, YouTube's daily budgets, the kept answers and the backoff.

A kept answer is returned without asking again while it is younger than its service's keep_days. A call past a
budget raises OverBudget with the moment the budget is new again; nothing is sent."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from . import VERSION, store

CONTACT_USER_AGENT = f'yt-playlist-maker/{VERSION} ( https://github.com/ernop/yt-playlist-maker )'
# Google knows the caller from its project's key or sign-in; it gets the app's name and nothing else.
GOOGLE_USER_AGENT = f'yt-playlist-maker/{VERSION}'
TIMEOUT = (10, 60)
BUSY_TRIES = 3
BUSY_WAIT_SECONDS = 10
PACIFIC = ZoneInfo('America/Los_Angeles')

# Half of each published limit (SPEC.md): search.list has its own 100 calls a day; everything else 10,000 units.
YOUTUBE_SEARCHES_PER_DAY = 50
YOUTUBE_UNITS_PER_DAY = 5000


@dataclass(frozen=True)
class Service:
    name: str
    interval: float  # seconds between two requests, at least
    keep_days: float  # how long an answer is kept; 0: never kept
    user_agent: str


SERVICES = {
    'musicbrainz': Service('MusicBrainz', 2.0, 90, CONTACT_USER_AGENT),
    'listenbrainz': Service('ListenBrainz', 2.0, 90, CONTACT_USER_AGENT),
    'wikidata': Service('Wikidata', 2.0, 90, CONTACT_USER_AGENT),
    'youtube': Service('YouTube', 0.0, 30, GOOGLE_USER_AGENT),
    'google': Service('Google sign-in', 0.0, 0, GOOGLE_USER_AGENT),
}


class OverBudget(RuntimeError):
    def __init__(self, what: str, until: datetime):
        super().__init__(f'{what} is used up for today; it is new again at {until.astimezone(PACIFIC):%H:%M} '
                         'Pacific time')
        self.until = until


class ServiceError(RuntimeError):
    def __init__(self, service: str, status: int, detail: str):
        super().__init__(f'{SERVICES[service].name} answered HTTP {status}: {detail}')
        self.service, self.status, self.detail = service, status, detail


class Unreachable(RuntimeError):
    pass


_locks = {name: threading.Lock() for name in SERVICES}
_last: dict[str, float] = {}
_session = requests.Session()


def now() -> datetime:
    return datetime.now(UTC)


def pacific_day(at: datetime | None = None) -> str:
    return (at or now()).astimezone(PACIFIC).date().isoformat()


def next_pacific_midnight(at: datetime | None = None) -> datetime:
    local = (at or now()).astimezone(PACIFIC)
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), PACIFIC)
    return midnight.astimezone(UTC)


def youtube_left() -> dict:
    """What is left of today's YouTube budgets."""
    day = pacific_day()
    return {
        'searches': YOUTUBE_SEARCHES_PER_DAY - store.usage('youtube-search', day),
        'searches_budget': YOUTUBE_SEARCHES_PER_DAY,
        'units': YOUTUBE_UNITS_PER_DAY - store.usage('youtube-units', day),
        'units_budget': YOUTUBE_UNITS_PER_DAY,
        'new_at': next_pacific_midnight().isoformat(),
    }


def call(service: str, method: str, url: str, *, params: dict | None = None, body: dict | None = None,
         form: dict | None = None, headers: dict | None = None, units: int = 0, search: bool = False,
         keep: bool = True, secret_params: tuple[str, ...] = ('key',)):
    """The JSON answer of one request. units and search are what it costs of YouTube's budgets."""
    spec = SERVICES[service]
    keep = keep and spec.keep_days > 0
    key = _key(method, url, params, body, form, secret_params)
    if keep:
        kept = store.answer(service, key, now() - timedelta(days=spec.keep_days))
        if kept is not None:
            return kept
    if units or search:
        _spend(units, search)
    reply = _send(service, method, url, params=params, body=body, form=form, headers=headers)
    if not reply.ok:
        raise ServiceError(service, reply.status_code, _detail(reply))
    try:
        data = reply.json()
    except ValueError as exc:
        raise ServiceError(service, reply.status_code, 'the answer is not JSON') from exc
    if keep:
        store.keep_answer(service, key, data)
    return data


def _spend(units: int, search: bool) -> None:
    """Count the cost before the request: YouTube charges every request, a refused one too."""
    day = pacific_day()
    if search and not store.spend('youtube-search', day, 1, YOUTUBE_SEARCHES_PER_DAY):
        raise OverBudget("YouTube's search budget", next_pacific_midnight())
    if units and not store.spend('youtube-units', day, units, YOUTUBE_UNITS_PER_DAY):
        if search:
            store.spend('youtube-search', day, -1, YOUTUBE_SEARCHES_PER_DAY)
        raise OverBudget("YouTube's daily units", next_pacific_midnight())


def _send(service: str, method: str, url: str, **kwargs) -> requests.Response:
    spec = SERVICES[service]
    headers = {'User-Agent': spec.user_agent, 'Accept': 'application/json', **(kwargs.pop('headers') or {})}
    body, form = kwargs.pop('body'), kwargs.pop('form')
    for attempt in range(BUSY_TRIES + 1):
        with _locks[service]:
            wait = _last.get(service, 0) + spec.interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                reply = _session.request(method, url, headers=headers, json=body, data=form, timeout=TIMEOUT,
                                         **kwargs)
            except requests.RequestException as exc:
                raise Unreachable(f'{spec.name} could not be reached: {type(exc).__name__}') from exc
            finally:
                _last[service] = time.monotonic()
        if reply.status_code not in (429, 503) or attempt == BUSY_TRIES:
            return reply
        time.sleep(_busy_wait(reply))
    return reply


def _busy_wait(reply: requests.Response) -> float:
    for header in ('Retry-After', 'X-RateLimit-Reset-In'):
        try:
            return min(max(float(reply.headers[header]), 1.0), 120.0)
        except (KeyError, ValueError):
            continue
    return BUSY_WAIT_SECONDS


def _key(method, url, params, body, form, secret_params) -> str:
    shown = {k: v for k, v in (params or {}).items() if k not in secret_params}
    text = json.dumps([method, url, shown, body, form], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def _detail(reply: requests.Response) -> str:
    try:
        data = reply.json()
    except ValueError:
        return reply.text[:200] or reply.reason
    if isinstance(data, dict):
        error = data.get('error')
        if isinstance(error, dict):
            return str(error.get('message') or error)[:300]
        return str(error or data.get('error_description') or data)[:300]
    return str(data)[:300]
