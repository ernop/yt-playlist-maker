"""Each song's YouTube video (SPEC.md, "How a song finds its video"): open data, then the artist's channel, then
search, each only for the songs still without one. match() is the rule for when a video is the song."""
from __future__ import annotations

import re
from datetime import timedelta

from . import musicbrainz, net, store, wikidata, youtube
from .reading import fold

CHECK_AFTER_DAYS = 25  # five days of hourly tries before KEEP_DAYS
KEEP_DAYS = 30  # YouTube's limit for kept API data
NEAR_S = 15
OWN_CHANNEL_LONGER_S = 90
NOT_THE_SONG = ('live', 'cover', 'karaoke', 'reaction', 'remix', 'instrumental', 'sped up', 'slowed', '8d', 'hour',
                'loop')
BRACKETS = re.compile(r'\s*[\(\[][^\)\]]*[\)\]]')


def core_title(title: str) -> str:
    """The song's name without what brackets add: '(Remastered 2009)', '[feat. X]'."""
    return fold(BRACKETS.sub('', title)) or fold(title)


def match(song: dict, video: dict, own_channels: set[str], *, title_needed: bool = True) -> tuple[int, int] | None:
    """How good the video is as the song, lower better (its channel's rank, then how far its length is from the
    recording's); None when it is not the song."""
    if not video['playable']:
        return None
    title = fold(video['title'])
    if title_needed and core_title(song['title']) not in title:
        return None
    words = f' {title} '
    song_words = f' {fold(song["title"])} '
    if any(f' {w} ' in words and f' {w} ' not in song_words for w in NOT_THE_SONG):
        return None
    own = video['channel_id'] in own_channels
    topic = fold(video['channel']) == fold(f'{song["artist"]} - Topic')
    gap = 0
    if song.get('length_ms') and video['duration_s'] is not None:
        gap = video['duration_s'] - round(song['length_ms'] / 1000)
        if not (-NEAR_S <= gap <= (OWN_CHANNEL_LONGER_S if own else NEAR_S)):
            return None
    return (0 if own else 1 if topic else 2), abs(gap)


def best(song: dict, details: dict[str, dict], ids: list[str], own_channels: set[str], *,
         title_needed: bool = True) -> str | None:
    ranked = sorted((rank, i, vid) for i, vid in enumerate(ids) if vid in details
                    for rank in [match(song, details[vid], own_channels, title_needed=title_needed)] if rank)
    return ranked[0][2] if ranked else None


def known() -> dict[str, dict]:
    with store.db() as con:
        return {row['recording_mbid']: dict(row) for row in con.execute('SELECT * FROM videos')}


def save(mbid: str, state: str, steps: list[str], video_id: str | None = None, source: str = '',
         detail: dict | None = None) -> None:
    with store.db() as con:
        con.execute(
            'INSERT OR REPLACE INTO videos (recording_mbid, state, video_id, source, looked, steps, yt_title, '
            'yt_channel, yt_duration_s, yt_checked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (mbid, state, video_id, source, store.stamp(), ','.join(steps),
             detail['title'] if detail else None, detail['channel'] if detail else None,
             detail['duration_s'] if detail else None, store.stamp() if detail else None))


def find(songs: list[dict], key: str, progress=None) -> None:
    """Look for the video of each song the app does not know yet. Raises net.OverBudget when YouTube's budget
    ends first: the songs left are looked for when it is new again."""
    have = known()
    todo = [s for s in {s['mbid']: s for s in songs}.values() if s['mbid'] not in have]
    if not todo:
        return
    if not key:
        raise youtube.NoKey('config.local.json has no youtube_api_key (docs/SETUP.md)')
    say = progress or (lambda text: None)

    open_ids: dict[str, list[tuple[str, str]]] = {}
    works: dict[str, list[str]] = {}
    for n, song in enumerate(todo, 1):
        say(f'MusicBrainz links of song {n} of {len(todo)}')
        links = musicbrainz.recording_links(song['mbid'])
        open_ids[song['mbid']] = [(v, 'musicbrainz') for v in links['youtube']]
        works[song['mbid']] = links['works']
    say('Wikidata videos')
    by_work = wikidata.videos_for_works([w for ws in works.values() for w in ws])
    for mbid, ws in works.items():
        for w in ws:
            open_ids[mbid] += [(v, 'wikidata') for v in by_work.get(w, []) if v not in {x for x, _ in open_ids[mbid]}]
    candidates = [v for pairs in open_ids.values() for v, _ in pairs]
    details = youtube.videos(candidates, key) if candidates else {}
    left = []
    for song in todo:
        ids = [v for v, _ in open_ids[song['mbid']]]
        chosen = best(song, details, ids, set(), title_needed=False)
        if chosen:
            source = dict(open_ids[song['mbid']])[chosen]
            save(song['mbid'], 'found', ['open'], chosen, source, details[chosen])
        else:
            left.append(song)

    channels: dict[str, dict | None] = {}
    still = []
    for n, song in enumerate(left, 1):
        say(f"the artist's channel, song {n} of {len(left)}")
        own, ids = set(), []
        for url in song.get('artist_youtube', []):
            if url not in channels:
                channels[url] = youtube.channel(url, key)
            found = channels[url]
            if found and found['uploads']:
                own.add(found['id'])
                ids += [u['video_id'] for u in youtube.uploads(found['uploads'], key)
                        if core_title(song['title']) in fold(u['title'])]
        song['own_channels'] = own
        found_details = youtube.videos(ids, key) if ids else {}
        chosen = best(song, found_details, ids, own)
        if chosen:
            save(song['mbid'], 'found', ['open', 'channel'], chosen, 'channel', found_details[chosen])
        else:
            still.append(song)

    for n, song in enumerate(still, 1):
        say(f'YouTube search, song {n} of {len(still)}')
        ids = youtube.search(f'{song["artist"]} {core_title(song["title"])}', key)
        found_details = youtube.videos(ids, key) if ids else {}
        chosen = best(song, found_details, ids, song['own_channels'])
        if chosen:
            save(song['mbid'], 'found', ['open', 'channel', 'search'], chosen, 'search', found_details[chosen])
        else:
            save(song['mbid'], 'none', ['open', 'channel', 'search'])


def refresh(key: str) -> dict:
    """YouTube's facts are checked again from CHECK_AFTER_DAYS on, and deleted when they reach KEEP_DAYS unchecked
    (Developer Policies III.E.4): a video YouTube no longer has, or no longer plays, is marked gone."""
    cutoff = store.iso(net.now() - timedelta(days=CHECK_AFTER_DAYS))
    last_day = store.iso(net.now() - timedelta(days=KEEP_DAYS))
    with store.db() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM videos WHERE yt_checked IS NOT NULL AND yt_checked < ?", (cutoff,))]
    if not rows:
        return {'checked': 0, 'gone': 0, 'deleted': 0}
    try:
        if not key:
            raise youtube.NoKey('no key')
        details = youtube.videos([r['video_id'] for r in rows], key, keep=False)
    except (youtube.NoKey, net.OverBudget, net.ServiceError, net.Unreachable):
        return {'checked': 0, 'gone': 0, 'deleted': _forget([r for r in rows if r['yt_checked'] < last_day])}
    gone = 0
    with store.db() as con:
        for row in rows:
            detail = details.get(row['video_id'])
            if detail and detail['playable']:
                con.execute('UPDATE videos SET yt_title = ?, yt_channel = ?, yt_duration_s = ?, yt_checked = ? '
                            'WHERE recording_mbid = ?', (detail['title'], detail['channel'], detail['duration_s'],
                                                         store.stamp(), row['recording_mbid']))
            else:
                gone += 1
                con.execute("UPDATE videos SET state = 'gone', video_id = NULL, yt_title = NULL, yt_channel = NULL, "
                            "yt_duration_s = NULL, yt_checked = NULL WHERE recording_mbid = ?", (row['recording_mbid'],))
    return {'checked': len(rows), 'gone': gone, 'deleted': 0}


def _forget(rows: list[dict]) -> int:
    """Delete what YouTube said: a video found by YouTube's own lists goes whole; one from open data keeps its id."""
    with store.db() as con:
        for row in rows:
            if row['source'] in ('channel', 'search'):
                con.execute('DELETE FROM videos WHERE recording_mbid = ?', (row['recording_mbid'],))
            else:
                con.execute('UPDATE videos SET yt_title = NULL, yt_channel = NULL, yt_duration_s = NULL, '
                            'yt_checked = NULL WHERE recording_mbid = ?', (row['recording_mbid'],))
    return len(rows)
