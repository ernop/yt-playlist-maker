"""Which songs each line gets (SPEC.md, "How songs are chosen", 2 to 4).

A line's pool is its candidates in order: an artist line's are artists ({'artist_mbid': mbid}), looked up only
when their turn comes; a style or description line's are recordings (songs, which have 'mbid'). pick() takes from the pool, in order, what the
rules allow, and leaves the rest for Swap."""
from __future__ import annotations

from . import listenbrainz
from .reading import fold

POOL_KEPT = 10
MIN_LENGTH_MS = 60_000  # shorter is an intro, a skit or an interlude, not the song he wants
TOP_PER_ARTIST = 10
SIMILAR_TAKEN = 40


class Used:
    """What the list has taken so far: recordings, songs (artist and title) and artists."""

    def __init__(self):
        self.recordings: set[str] = set()
        self.songs: set[tuple[str, str]] = set()
        self.artists: set[str] = set()

    def take(self, song: dict) -> None:
        self.recordings.add(song['mbid'])
        self.songs.add((fold(song['artist']), fold(song['title'])))
        self.artists.update(song['artist_mbids'])

    def allows(self, song: dict) -> bool:
        return song['mbid'] not in self.recordings and (fold(song['artist']), fold(song['title'])) not in self.songs

    def has_artist(self, entry: dict) -> bool:
        return bool(_artists(entry) & self.artists)


def _artists(entry: dict) -> set[str]:
    return {entry['artist_mbid']} if 'artist_mbid' in entry else set(entry['artist_mbids'])


def pool(reading: dict) -> list[dict]:
    """The line's candidates, best first."""
    seeds = [a for a in reading['artists'] if a.get('mbid')]
    if reading['kind'] == 'artist':
        entries, seen = [], {a['mbid'] for a in seeds}
        for seed in seeds:
            for similar in listenbrainz.similar_artists(seed['mbid'])[:SIMILAR_TAKEN]:
                if similar['mbid'] not in seen:
                    seen.add(similar['mbid'])
                    entries.append({'artist_mbid': similar['mbid'], 'name': similar['name'],
                                    'why': f'like {seed["name"]}'})
        return entries
    return _style_recordings(reading, seeds)


def _style_recordings(reading: dict, seeds: list[dict]) -> list[dict]:
    why: dict[str, str] = {}
    listens: dict[str, int] = {}
    for tag in reading['tags']:
        for mbid in listenbrainz.tag_recordings(tag):
            why.setdefault(mbid, f'tagged {tag}')
    if why:
        listens.update(listenbrainz.recording_listens(list(why)))
    for seed in seeds:
        for row in listenbrainz.top_recordings(seed['mbid'])[:TOP_PER_ARTIST]:
            why.setdefault(row['mbid'], f'by {seed["name"]}, named for this line')
            listens[row['mbid']] = max(listens.get(row['mbid'], 0), row['listens'])
    meta = listenbrainz.recording_metadata(list(why))
    songs = [_song(meta[mbid], why[mbid], listens.get(mbid, 0)) for mbid in why if mbid in meta]
    songs = [s for s in songs if _fits(s, reading['years'])]
    songs.sort(key=lambda s: -s['listens'])
    return songs


def _song(meta: dict, why: str, listens: int) -> dict:
    return {
        'mbid': meta['mbid'],
        'title': meta['name'],
        'artist': meta['artist'],
        'artist_mbids': [a['mbid'] for a in meta['artists'] if a['mbid']],
        'artist_youtube': [a['youtube'] for a in meta['artists'] if a['youtube']],
        'year': meta['year'],
        'length_ms': meta['length_ms'],
        'listens': listens,
        'why': why,
    }


def _fits(song: dict, years: list[int] | None) -> bool:
    if not song['title'] or not song['artist_mbids']:
        return False
    if song['length_ms'] is not None and song['length_ms'] < MIN_LENGTH_MS:
        return False
    if years:
        return song['year'] is not None and years[0] <= song['year'] <= years[1]
    return True


def _song_of(entry: dict, years: list[int] | None, used: Used) -> dict | None:
    """The entry's song: a recording entry itself, an artist entry its most-listened recording the rules allow."""
    if 'artist_mbid' not in entry:
        return entry if used.allows(entry) else None
    top = listenbrainz.top_recordings(entry['artist_mbid'])[:TOP_PER_ARTIST]
    meta = listenbrainz.recording_metadata([r['mbid'] for r in top]) if top else {}
    for row in top:
        if row['mbid'] in meta:
            song = _song(meta[row['mbid']], entry['why'], row['listens'])
            if _fits(song, years) and used.allows(song):
                return song
    return None


def pick(entries: list[dict], years: list[int] | None, count: int, used: Used,
         line_artists: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """count songs from the pool, and what is left of it (POOL_KEPT at most). One song per artist in a line; an
    artist another line has taken waits until the pool has no other."""
    line_artists = set() if line_artists is None else line_artists
    picked: list[dict] = []
    rest: list[dict] = []
    waiting: list[dict] = []
    for entry in entries:
        if len(picked) >= count:
            rest.append(entry)
        elif _artists(entry) & line_artists:
            continue
        elif used.has_artist(entry):
            waiting.append(entry)
        else:
            _take(entry, years, used, line_artists, picked)
    for entry in waiting:
        if len(picked) >= count:
            rest.append(entry)
        elif not _artists(entry) & line_artists:
            _take(entry, years, used, line_artists, picked)
    return picked, rest[:POOL_KEPT]


def _take(entry: dict, years: list[int] | None, used: Used, line_artists: set[str], picked: list[dict]) -> None:
    song = _song_of(entry, years, used)
    if song is not None and not set(song['artist_mbids']) & line_artists:
        picked.append(song)
        used.take(song)
        line_artists.update(song['artist_mbids'])
