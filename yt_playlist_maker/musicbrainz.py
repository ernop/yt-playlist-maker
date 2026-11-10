"""MusicBrainz's web service (musicbrainz.org/doc/MusicBrainz_API): artist names, and a recording's links."""
from __future__ import annotations

import re

from . import net

API = 'https://musicbrainz.org/ws/2'
YOUTUBE_ID = re.compile(r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([-_0-9A-Za-z]{11})')


def search_artists(name: str) -> list[dict]:
    """MusicBrainz's artists for a name, best match first: mbid, name, sort_name, comment, score, aliases."""
    quoted = name.replace('\\', '\\\\').replace('"', '\\"')
    data = net.call('musicbrainz', 'GET', f'{API}/artist',
                    params={'query': f'artist:"{quoted}" OR alias:"{quoted}"', 'fmt': 'json', 'limit': 10})
    return [{
        'mbid': a['id'],
        'name': a['name'],
        'sort_name': a.get('sort-name', ''),
        'comment': a.get('disambiguation', ''),
        'score': int(a.get('score', 0)),
        'aliases': [x['name'] for x in a.get('aliases') or []],
    } for a in data.get('artists', [])]


def recording_links(mbid: str) -> dict:
    """The YouTube video ids MusicBrainz links to the recording, and the works it records."""
    data = net.call('musicbrainz', 'GET', f'{API}/recording/{mbid}',
                    params={'inc': 'url-rels work-rels', 'fmt': 'json'})
    youtube, works = [], []
    for rel in data.get('relations', []):
        match = YOUTUBE_ID.search((rel.get('url') or {}).get('resource', ''))
        if match and match.group(1) not in youtube:
            youtube.append(match.group(1))
        if rel.get('target-type') == 'work' and rel.get('work'):
            works.append(rel['work']['id'])
    return {'youtube': youtube, 'works': works}
