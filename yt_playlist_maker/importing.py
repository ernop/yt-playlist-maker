"""Import a direct song list from exported JSON (voice-wei-library or generic lists), YouTube video or playlist
URLs/IDs, or plain 'Artist - Song' text lines."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

MAX_DIRECT_SONGS = 5000

VIDEO_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')
BARE_PLAYLIST_ID_RE = re.compile(r'^(?:PL|UU|LL|FL|OLAK5uy_|RDCLAK5uy_)[A-Za-z0-9_-]{10,}$')
URL_PLAYLIST_ID_RE = re.compile(r'^[A-Za-z0-9_-]{12,}$')
YOUTUBE_HOSTS = frozenset({
    'youtube.com',
    'www.youtube.com',
    'm.youtube.com',
    'music.youtube.com',
})
YOUTU_BE_HOSTS = frozenset({'youtu.be', 'www.youtu.be'})
SEP_RE = re.compile(r'\s+(?:-|\u2013|\u2014)\s+')


def extract_video_id(text: str) -> str | None:
    """Return an 11-character YouTube video id from a URL or bare id, or None."""
    raw = str(text or '').strip()
    if not raw:
        return None
    if VIDEO_ID_RE.fullmatch(raw):
        return raw
    if not raw.startswith(('http://', 'https://')):
        return None
    parsed = urlparse(raw)
    host = (parsed.hostname or '').lower()
    if host in YOUTU_BE_HOSTS:
        part = parsed.path.strip('/').split('/')[0]
        return part if VIDEO_ID_RE.fullmatch(part) else None
    if host in YOUTUBE_HOSTS:
        parts = [p for p in parsed.path.split('/') if p]
        if not parts or parts[0] == 'watch':
            vid = (parse_qs(parsed.query).get('v') or [''])[0].strip()
            return vid if VIDEO_ID_RE.fullmatch(vid) else None
        if parts[0] in ('shorts', 'embed', 'live', 'v') and len(parts) >= 2:
            vid = parts[1].strip()
            return vid if VIDEO_ID_RE.fullmatch(vid) else None
    return None


def extract_playlist_id(text: str) -> str | None:
    """Return a YouTube playlist id from a playlist URL or bare playlist id, or None."""
    raw = str(text or '').strip()
    if not raw:
        return None
    if BARE_PLAYLIST_ID_RE.fullmatch(raw):
        return raw
    if not raw.startswith(('http://', 'https://')):
        return None
    parsed = urlparse(raw)
    host = (parsed.hostname or '').lower()
    if host not in YOUTUBE_HOSTS and host not in YOUTU_BE_HOSTS:
        return None
    pid = (parse_qs(parsed.query).get('list') or [''])[0].strip()
    return pid if URL_PLAYLIST_ID_RE.fullmatch(pid) else None


def _clean_year(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if 1000 <= value <= 2999 else None
    match = re.search(r'\b(1\d{3}|2\d{3})\b', str(value))
    return int(match.group(1)) if match else None


def _clean_length_ms(item: dict) -> int | None:
    if isinstance(item.get('length_ms'), (int, float)) and item['length_ms'] > 0:
        return int(item['length_ms'])
    for key in ('durationSeconds', 'duration_seconds', 'duration_s'):
        val = item.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return int(round(val * 1000))
    return None


def _song_from_dict(item: dict, *, default_source: str) -> dict | None:
    if not isinstance(item, dict):
        return None
    vid = (
        extract_video_id(item.get('videoId', ''))
        or extract_video_id(item.get('video_id', ''))
        or extract_video_id(item.get('url', ''))
        or extract_video_id(item.get('id', ''))
        or ''
    )
    title = str(item.get('name') or item.get('title') or '').strip()
    artist = str(item.get('artist') or item.get('channelTitle') or item.get('channel') or '').strip()
    if not vid and not title:
        return None
    return {
        'video_id': vid,
        'title': title or vid,
        'artist': artist,
        'year': _clean_year(item.get('year')),
        'length_ms': _clean_length_ms(item),
        'source': default_source,
    }


def _split_artist_title(text: str) -> tuple[str, str]:
    cleaned = ' '.join(text.split())
    parts = SEP_RE.split(cleaned, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0].strip(), parts[1].strip()
    return '', cleaned


def _parse_line(line: str, songs: list[dict], playlist_ids: list[str]) -> None:
    stripped = line.strip()
    if not stripped or stripped.startswith('#'):
        return
    comment = ''
    head = stripped
    if '\t' in stripped:
        head, comment = stripped.split('\t', 1)
        head, comment = head.strip(), comment.strip()
    elif ' #' in stripped:
        maybe_head, maybe_comment = stripped.split(' #', 1)
        if extract_video_id(maybe_head.strip()) or extract_playlist_id(maybe_head.strip()):
            head, comment = maybe_head.strip(), maybe_comment.strip()
    vid = extract_video_id(head)
    if vid:
        artist, title = _split_artist_title(comment) if comment else ('', vid)
        songs.append({
            'video_id': vid,
            'title': title,
            'artist': artist,
            'year': None,
            'length_ms': None,
            'source': 'url',
        })
        return
    pid = extract_playlist_id(head)
    if pid:
        playlist_ids.append(pid)
        return
    artist, title = _split_artist_title(stripped)
    songs.append({
        'video_id': '',
        'title': title,
        'artist': artist,
        'year': None,
        'length_ms': None,
        'source': 'text',
    })


def _dedupe_songs(songs: list[dict]) -> list[dict]:
    seen_ids: set[str] = set()
    seen_text: set[tuple[str, str]] = set()
    out: list[dict] = []
    for item in songs:
        vid = item['video_id']
        if vid:
            if vid in seen_ids:
                continue
            seen_ids.add(vid)
            out.append(item)
        else:
            key = (item['artist'].lower(), item['title'].lower())
            if not key[1] or key in seen_text:
                continue
            seen_text.add(key)
            out.append(item)
    return out


def parse(raw_text: str) -> dict:
    """Parse voice-wei JSON, generic JSON, YouTube links/IDs, or plain text lines into normalized songs."""
    text = str(raw_text or '').lstrip('\ufeff').strip()
    if not text:
        raise ValueError('Paste a song list, YouTube links, or exported JSON')

    songs: list[dict] = []
    playlist_ids: list[str] = []
    title = ''
    fmt = 'text'

    if text[0] in '{[':
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ValueError(f'Invalid JSON: {exc}') from exc
        if isinstance(data, dict) and data.get('format') == 'voice-wei-library':
            fmt = 'voice-wei-library'
            title = str(data.get('title') or '').strip()
            raw_songs = data.get('songs') if isinstance(data.get('songs'), list) else []
            for entry in raw_songs:
                parsed = _song_from_dict(entry, default_source='voice-wei')
                if parsed:
                    songs.append(parsed)
            favs = data.get('favorites') if isinstance(data.get('favorites'), dict) else {}
            if favs:
                ordered = sorted(
                    favs.items(),
                    key=lambda kv: (kv[1].get('favoritedAt', 0) if isinstance(kv[1], dict) else 0),
                )
                for vid, entry in ordered:
                    if not isinstance(entry, dict):
                        continue
                    item = {'videoId': vid, **entry}
                    parsed = _song_from_dict(item, default_source='voice-wei')
                    if parsed:
                        songs.append(parsed)
        elif isinstance(data, list):
            fmt = 'json'
            for entry in data:
                if isinstance(entry, str):
                    _parse_line(entry, songs, playlist_ids)
                else:
                    parsed = _song_from_dict(entry, default_source='json')
                    if parsed:
                        songs.append(parsed)
        elif isinstance(data, dict):
            fmt = 'json'
            title = str(data.get('title') or data.get('name') or '').strip()
            seq = None
            for key in ('songs', 'tracks', 'items', 'videos'):
                if isinstance(data.get(key), list):
                    seq = data[key]
                    break
            if seq is None:
                raise ValueError('JSON object must contain a songs, tracks, items, or videos list')
            for entry in seq:
                if isinstance(entry, str):
                    _parse_line(entry, songs, playlist_ids)
                else:
                    parsed = _song_from_dict(entry, default_source='json')
                    if parsed:
                        songs.append(parsed)
        else:
            raise ValueError('JSON input must be a list or an object')
    else:
        for line in text.splitlines():
            _parse_line(line, songs, playlist_ids)
        if songs and all(s['source'] == 'url' for s in songs):
            fmt = 'youtube-urls'
        elif playlist_ids and not songs:
            fmt = 'youtube-playlist'

    songs = _dedupe_songs(songs)
    playlist_ids = list(dict.fromkeys(playlist_ids))
    if not songs and not playlist_ids:
        raise ValueError('No songs or YouTube links found in the input')
    if len(songs) > MAX_DIRECT_SONGS:
        raise ValueError(f'At most {MAX_DIRECT_SONGS} songs per import; this has {len(songs)}')

    return {
        'format': fmt,
        'title': title,
        'songs': songs,
        'playlist_ids': playlist_ids,
    }
