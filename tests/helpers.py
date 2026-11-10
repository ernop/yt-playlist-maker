"""Fakes and builders the tests share."""
from __future__ import annotations

import json


class FakeReply:
    def __init__(self, status: int = 200, data=None, headers: dict | None = None):
        self.status_code = status
        self.ok = status < 400
        self._data = data
        self.headers = headers or {}
        self.text = json.dumps(data)
        self.reason = 'reason'

    def json(self):
        return self._data


class FakeSession:
    def __init__(self):
        self.routes: list[tuple[str, list[FakeReply]]] = []
        self.calls: list[dict] = []
        self.slept: list[float] = []

    def route(self, part: str, *replies: FakeReply) -> None:
        """Requests whose URL holds part get these replies in turn; the last one repeats."""
        self.routes.append((part, list(replies)))

    def request(self, method, url, headers=None, json=None, data=None, timeout=None, params=None):
        self.calls.append({'method': method, 'url': url, 'headers': headers, 'json': json, 'data': data,
                           'params': params})
        for part, replies in self.routes:
            if part in url:
                return replies.pop(0) if len(replies) > 1 else replies[0]
        raise AssertionError(f'unexpected request: {method} {url}')


def song(mbid: str, artist: str, title: str, *, artist_mbids=None, year=2000, length_ms=200_000, listens=0,
         why='test') -> dict:
    return {'mbid': mbid, 'title': title, 'artist': artist, 'artist_mbids': artist_mbids or [f'a-{artist}'],
            'artist_youtube': [], 'year': year, 'length_ms': length_ms, 'listens': listens, 'why': why}


def video(title: str, *, channel='Some Channel', channel_id='UCother', duration_s=200, playable=True) -> dict:
    return {'title': title, 'channel': channel, 'channel_id': channel_id, 'duration_s': duration_s,
            'playable': playable}
