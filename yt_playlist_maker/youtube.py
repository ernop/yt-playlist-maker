"""The YouTube Data API v3 (developers.google.com/youtube/v3). Costs from its quota table
(developers.google.com/youtube/v3/determine_quota_cost, read 2026-11-09): search.list 1 of its own 100 a day;
videos, channels, playlistItems and playlists list 1 unit; playlists and playlistItems insert 50 units."""
from __future__ import annotations

import re

from . import net

API = 'https://www.googleapis.com/youtube/v3'
PAGE = 50
UPLOAD_PAGES = 20  # an artist's 1,000 newest uploads at most: 20 units
DURATION = re.compile(r'^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$')
COUNTRY = 'US'


class NoKey(RuntimeError):
    pass


def seconds(iso_duration: str) -> int | None:
    """An ISO 8601 duration (PT3M45S) in seconds."""
    match = DURATION.match(iso_duration or '')
    if not match:
        return None
    days, hours, minutes, secs = (int(g or 0) for g in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + secs


def _read(path: str, params: dict, key: str, *, units: int = 1, search: bool = False, keep: bool = True,
          token: str = ''):
    if not key and not token:
        raise NoKey('config.local.json has no youtube_api_key')
    headers = {'Authorization': f'Bearer {token}'} if token else None
    if not token:
        params = {**params, 'key': key}
    return net.call('youtube', 'GET', f'{API}/{path}', params=params, headers=headers, units=units, search=search,
                    keep=keep)


def videos(ids: list[str], key: str, *, keep: bool = True) -> dict[str, dict]:
    """What YouTube says of each video it still has, by id: title, channel_id, channel, duration_s, playable."""
    found = {}
    unique = list(dict.fromkeys(ids))
    for start in range(0, len(unique), PAGE):
        chunk = unique[start:start + PAGE]
        data = _read('videos', {'part': 'snippet,contentDetails,status', 'id': ','.join(chunk),
                                'maxResults': PAGE}, key, keep=keep)
        for item in data.get('items', []):
            snippet, details, status = item.get('snippet', {}), item.get('contentDetails', {}), item.get('status', {})
            region = details.get('regionRestriction') or {}
            blocked = COUNTRY in (region.get('blocked') or []) or ('allowed' in region and
                                                                  COUNTRY not in region['allowed'])
            found[item['id']] = {
                'title': snippet.get('title', ''),
                'channel_id': snippet.get('channelId', ''),
                'channel': snippet.get('channelTitle', ''),
                'duration_s': seconds(details.get('duration', '')),
                'playable': status.get('privacyStatus') == 'public' and status.get('uploadStatus') == 'processed'
                            and not blocked,
            }
    return found


def channel(url: str, key: str) -> dict | None:
    """The channel a youtube.com address names: id, title and its uploads list; None when YouTube has none."""
    match = re.search(r'youtube\.com/(channel/(UC[-_0-9A-Za-z]{22})|user/([^/?#]+)|@([^/?#]+))', url or '')
    if not match:
        return None
    if match.group(2):
        params = {'id': match.group(2)}
    elif match.group(3):
        params = {'forUsername': match.group(3)}
    else:
        params = {'forHandle': '@' + match.group(4)}
    data = _read('channels', {'part': 'snippet,contentDetails', **params}, key)
    items = data.get('items') or []
    if not items:
        return None
    item = items[0]
    return {'id': item['id'], 'title': item.get('snippet', {}).get('title', ''),
            'uploads': item.get('contentDetails', {}).get('relatedPlaylists', {}).get('uploads', '')}


def uploads(playlist_id: str, key: str) -> list[dict]:
    """The channel's newest uploads, UPLOAD_PAGES pages at most: video_id, title."""
    found, page = [], ''
    for _ in range(UPLOAD_PAGES):
        params = {'part': 'snippet', 'playlistId': playlist_id, 'maxResults': PAGE}
        if page:
            params['pageToken'] = page
        try:
            data = _read('playlistItems', params, key)
        except net.ServiceError as exc:
            if exc.status == 404:
                return found
            raise
        for item in data.get('items', []):
            snippet = item.get('snippet', {})
            video = (snippet.get('resourceId') or {}).get('videoId')
            if video:
                found.append({'video_id': video, 'title': snippet.get('title', '')})
        page = data.get('nextPageToken', '')
        if not page:
            break
    return found


def search(query: str, key: str) -> list[str]:
    """The first 10 music videos YouTube finds for the query: one of the day's searches."""
    data = _read('search', {'part': 'snippet', 'q': query, 'type': 'video', 'videoCategoryId': '10',
                            'maxResults': 10, 'regionCode': COUNTRY}, key, units=0, search=True)
    return [item['id']['videoId'] for item in data.get('items', []) if item.get('id', {}).get('videoId')]


def playlist_exists(playlist_id: str, key: str, token: str = '') -> bool:
    """Whether YouTube still has the playlist: 1 unit. A private one is seen only with his sign-in."""
    data = _read('playlists', {'part': 'id', 'id': playlist_id}, key, keep=False, token=token)
    return bool(data.get('items'))


def playlist_url(playlist_id: str) -> str:
    return f'https://www.youtube.com/playlist?list={playlist_id}'


def create_playlist(token: str, title: str, description: str, visibility: str) -> str:
    data = net.call('youtube', 'POST', f'{API}/playlists', params={'part': 'snippet,status'},
                    headers={'Authorization': f'Bearer {token}'}, units=50, keep=False,
                    body={'snippet': {'title': title, 'description': description},
                          'status': {'privacyStatus': visibility}})
    return data['id']


def add_to_playlist(token: str, playlist_id: str, video_id: str, position: int) -> None:
    net.call('youtube', 'POST', f'{API}/playlistItems', params={'part': 'snippet'},
             headers={'Authorization': f'Bearer {token}'}, units=50, keep=False,
             body={'snippet': {'playlistId': playlist_id, 'position': position,
                               'resourceId': {'kind': 'youtube#video', 'videoId': video_id}}})


def playlist_videos(token: str, playlist_id: str) -> list[str] | None:
    """The videos in his playlist, in order; None when YouTube no longer has it."""
    found, page = [], ''
    while True:
        params = {'part': 'contentDetails', 'playlistId': playlist_id, 'maxResults': PAGE}
        if page:
            params['pageToken'] = page
        try:
            data = _read('playlistItems', params, '', keep=False, token=token)
        except net.ServiceError as exc:
            if exc.status == 404:
                return None
            raise
        found += [item['contentDetails']['videoId'] for item in data.get('items', [])]
        page = data.get('nextPageToken', '')
        if not page:
            return found
