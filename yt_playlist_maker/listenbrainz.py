"""ListenBrainz's documented endpoints (listenbrainz.readthedocs.io, labs.api.listenbrainz.org): similar
artists, an artist's most-listened recordings, tag radio, recording metadata and popularity. Every request to its
API carries his user token, which its popularity endpoints require (SPEC.md, decision 2); labs takes none."""
from __future__ import annotations

from . import net

API = 'https://api.listenbrainz.org/1'
LABS = 'https://labs.api.listenbrainz.org'
# The dataset LB Radio's artist similarity reads (labs.api.listenbrainz.org/similar-artists).
SIMILAR_ARTISTS = 'session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30'
BATCH = 200

_token = ''


class NoToken(RuntimeError):
    pass


def use_token(token: str) -> None:
    """His ListenBrainz user token, from config.local.json."""
    global _token
    _token = token


def _api(method: str, path: str, **kwargs):
    if not _token:
        raise NoToken('config.local.json has no listenbrainz_token (docs/SETUP.md)')
    return net.call('listenbrainz', method, f'{API}{path}', headers={'Authorization': f'Token {_token}'}, **kwargs)


def similar_artists(mbid: str) -> list[dict]:
    """The artists most like this one, most similar first: mbid, name, comment, score."""
    data = net.call('listenbrainz', 'GET', f'{LABS}/similar-artists/json',
                    params={'artist_mbids': mbid, 'algorithm': SIMILAR_ARTISTS})
    rows = [r for r in data if r.get('reference_mbid') == mbid and r.get('artist_mbid') != mbid]
    rows.sort(key=lambda r: -r.get('score', 0))
    return [{'mbid': r['artist_mbid'], 'name': r['name'], 'comment': r.get('comment') or '',
             'score': r.get('score', 0)} for r in rows]


def top_recordings(artist_mbid: str) -> list[dict]:
    """The artist's recordings, most listened first: mbid, name, artist, artist_mbids, length_ms, listens."""
    try:
        data = _api('GET', f'/popularity/top-recordings-for-artist/{artist_mbid}')
    except net.ServiceError as exc:
        if exc.status in (400, 404):
            return []
        raise
    return [{
        'mbid': r['recording_mbid'],
        'name': r['recording_name'],
        'artist': r.get('artist_name') or '',
        'artist_mbids': r.get('artist_mbids') or [],
        'length_ms': r.get('length'),
        'listens': r.get('total_listen_count') or 0,
    } for r in data if r.get('recording_mbid')]


def tag_recordings(tag: str, count: int = 100) -> list[str]:
    """Recordings tagged so, in MusicBrainz, as LB Radio's tag radio picks them."""
    data = _api('GET', '/lb-radio/tags',
                params={'tag': tag, 'operator': 'or', 'pop_begin': 0, 'pop_end': 100, 'count': count})
    return [r['recording_mbid'] for r in data if r.get('recording_mbid')]


def recording_metadata(mbids: list[str]) -> dict[str, dict]:
    """Each recording's name, length, first release date and artists with their links, by mbid."""
    found = {}
    for start in range(0, len(mbids), BATCH):
        chunk = sorted(set(mbids[start:start + BATCH]))
        data = _api('POST', '/metadata/recording/', body={'recording_mbids': chunk, 'inc': 'artist release'})
        for mbid, meta in data.items():
            recording = meta.get('recording') or {}
            artist = meta.get('artist') or {}
            date = recording.get('first_release_date') or ''
            found[mbid] = {
                'mbid': mbid,
                'name': recording.get('name') or '',
                'length_ms': recording.get('length'),
                'year': int(date[:4]) if date[:4].isdigit() else (meta.get('release') or {}).get('year'),
                'artist': artist.get('name') or '',
                'artists': [{'mbid': a.get('artist_mbid'), 'name': a.get('name') or '',
                             'youtube': (a.get('rels') or {}).get('youtube') or ''}
                            for a in artist.get('artists') or []],
            }
    return found


def recording_listens(mbids: list[str]) -> dict[str, int]:
    found = {}
    for start in range(0, len(mbids), BATCH):
        chunk = sorted(set(mbids[start:start + BATCH]))
        data = _api('POST', '/popularity/recording', body={'recording_mbids': chunk})
        for row in data:
            found[row['recording_mbid']] = row.get('total_listen_count') or 0
    return found


def artist_listens(mbids: list[str]) -> dict[str, int]:
    data = _api('POST', '/popularity/artist', body={'artist_mbids': sorted(set(mbids))})
    return {row['artist_mbid']: row.get('total_listen_count') or 0 for row in data}
