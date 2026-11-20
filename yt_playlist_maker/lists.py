"""His lists: either similar-song prompts (reading, choosing, videos) or direct imported song lists (videos only).
The worker moves a list through its steps; a step that fails keeps its step and says why in error."""
from __future__ import annotations

import hashlib
import json

from . import choosing, google_auth, importing, reading, store, videos, youtube
from .config import Config

MAX_LINES = 20
MAX_LINE_CHARS = 200
SONGS = (2, 3, 4, 5)
STEPS = ('reading', 'choosing', 'videos', 'ready')


class Failed(RuntimeError):
    """A step cannot be done, and trying again later will not change that."""


def split(text: str) -> list[str]:
    lines = list(dict.fromkeys(' '.join(line.split()) for line in text.splitlines() if line.strip()))
    if not lines:
        raise ValueError('Type at least one line')
    if len(lines) > MAX_LINES:
        raise ValueError(f'At most {MAX_LINES} lines; this has {len(lines)}')
    too_long = [n for n, line in enumerate(lines, 1) if len(line) > MAX_LINE_CHARS]
    if too_long:
        raise ValueError(f'Line {too_long[0]} is longer than {MAX_LINE_CHARS} characters')
    return lines


def create(text: str, songs_per_line: int) -> int:
    if songs_per_line not in SONGS:
        raise ValueError(f'Songs per line must be one of {SONGS}')
    lines = split(text)
    with store.db() as con:
        list_id = con.execute(
            "INSERT INTO lists (created, text, songs_per_line, mode, step) VALUES (?, ?, ?, 'similar', 'reading')",
            (store.stamp(), text, songs_per_line),
        ).lastrowid
        con.executemany(
            'INSERT INTO lines (list_id, position, text) VALUES (?, ?, ?)',
            [(list_id, n, line) for n, line in enumerate(lines)],
        )
    return list_id


def _direct_mbid(song: dict) -> str:
    if song['video_id']:
        return f'yt:{song["video_id"]}'
    digest = hashlib.sha256(f'{song["artist"].lower()}\t{song["title"].lower()}'.encode()).hexdigest()[:24]
    return f'text:{digest}'


def create_direct(raw_text: str, *, title: str = '', target_playlist: str = '') -> int:
    """Create a direct song-list job from JSON, YouTube links/IDs, playlist URLs, or 'Artist - Song' lines."""
    parsed = importing.parse(raw_text)
    target_pid = ''
    if target_playlist and target_playlist.strip():
        target_pid = importing.extract_playlist_id(target_playlist) or ''
        if not target_pid:
            raise ValueError('Target playlist must be a YouTube playlist URL or playlist ID')
    list_title = title.strip() or parsed['title'] or 'Imported songs'
    reading_meta = {
        'kind': 'import',
        'format': parsed['format'],
        'playlist_ids': parsed['playlist_ids'],
    }
    why_map = {
        'voice-wei': 'From voice-wei',
        'json': 'Imported JSON',
        'url': 'Imported YouTube link',
        'text': 'Imported song line',
    }
    with store.db() as con:
        list_id = con.execute(
            "INSERT INTO lists (created, text, songs_per_line, mode, title, target_playlist_id, step) "
            "VALUES (?, ?, 0, 'direct', ?, ?, 'videos')",
            (store.stamp(), list_title, list_title, target_pid),
        ).lastrowid
        line_id = con.execute(
            'INSERT INTO lines (list_id, position, text, reading, chosen) VALUES (?, 0, ?, ?, 1)',
            (list_id, list_title, json.dumps(reading_meta, ensure_ascii=False)),
        ).lastrowid
        for position, item in enumerate(parsed['songs']):
            mbid = _direct_mbid(item)
            _insert(con, line_id, position, {
                'mbid': mbid,
                'title': item['title'],
                'artist': item['artist'],
                'artist_mbids': [],
                'artist_youtube': [],
                'year': item['year'],
                'length_ms': item['length_ms'],
                'why': why_map.get(item['source'], 'Imported song'),
            })
    return list_id


def get(list_id: int) -> dict:
    with store.db() as con:
        row = con.execute('SELECT * FROM lists WHERE id = ?', (list_id,)).fetchone()
        if row is None:
            raise KeyError(list_id)
        lines = [dict(r) for r in con.execute('SELECT * FROM lines WHERE list_id = ? ORDER BY position', (list_id,))]
        picks = [dict(r) for r in con.execute(
            'SELECT picks.* FROM picks JOIN lines ON lines.id = picks.line_id WHERE lines.list_id = ? '
            'AND picks.removed = 0 ORDER BY picks.position', (list_id,))]
        found = {r['recording_mbid']: dict(r) for r in con.execute(
            'SELECT videos.* FROM videos JOIN picks ON picks.recording_mbid = videos.recording_mbid '
            'JOIN lines ON lines.id = picks.line_id WHERE lines.list_id = ?', (list_id,))}
        playlist = con.execute('SELECT * FROM playlists WHERE list_id = ?', (list_id,)).fetchone()
    whole = dict(row)
    for line in lines:
        line['reading'] = json.loads(line['reading']) if line['reading'] else None
        line['pool_size'] = len(json.loads(line.pop('pool')))
        line['picks'] = []
    by_line = {line['id']: line for line in lines}
    for pick in picks:
        pick['artist_mbids'] = json.loads(pick['artist_mbids'])
        pick['artist_youtube'] = json.loads(pick['artist_youtube'])
        pick['video'] = found.get(pick['recording_mbid'])
        by_line[pick['line_id']]['picks'].append(pick)
    whole['lines'] = lines
    whole['playlist'] = dict(playlist) if playlist else None
    if whole['playlist'] and whole['playlist']['youtube_id']:
        whole['playlist']['url'] = youtube.playlist_url(whole['playlist']['youtube_id'])
    return whole


def summaries() -> list[dict]:
    with store.db() as con:
        rows = con.execute(
            'SELECT lists.id, lists.created, lists.mode, lists.title, lists.step, lists.error, '
            'playlists.youtube_id, playlists.step AS saved, '
            '(SELECT count(*) FROM picks JOIN lines ON lines.id = picks.line_id '
            'WHERE lines.list_id = lists.id AND picks.removed = 0) AS song_count, '
            '(SELECT group_concat(text, ", ") FROM (SELECT text FROM lines WHERE list_id = lists.id '
            'ORDER BY position)) AS lines FROM lists LEFT JOIN playlists ON playlists.list_id = lists.id '
            'ORDER BY lists.id DESC LIMIT 100').fetchall()
    out = []
    for r in rows:
        item = dict(r)
        if item['mode'] == 'direct' and item['title']:
            item['lines'] = f'{item["title"]} ({item["song_count"]} songs)'
        item['url'] = youtube.playlist_url(r['youtube_id']) if r['youtube_id'] else ''
        out.append(item)
    return out


def set_step(list_id: int, step: str, **fields) -> None:
    fields = {'step': step, 'error': '', 'wait_until': None, 'wait_reason': '', 'tries': 0, 'progress': '', **fields}
    names = ', '.join(f'{k} = ?' for k in fields)
    with store.db() as con:
        con.execute(f'UPDATE lists SET {names} WHERE id = ?', (*fields.values(), list_id))


def note(list_id: int, **fields) -> None:
    names = ', '.join(f'{k} = ?' for k in fields)
    with store.db() as con:
        con.execute(f'UPDATE lists SET {names} WHERE id = ?', (*fields.values(), list_id))


def read_step(cfg: Config, list_id: int) -> None:
    whole = get(list_id)
    if whole['mode'] == 'direct':
        set_step(list_id, 'videos')
        return
    note(list_id, progress=f'Reading {len(whole["lines"])} lines')
    readings = reading.read([line['text'] for line in whole['lines']], cfg.language_model)
    with store.db() as con:
        for line, found in zip(whole['lines'], readings):
            con.execute("UPDATE lines SET reading = ?, chosen = 0, pool = '[]', error = ? WHERE id = ?",
                        (json.dumps(found, ensure_ascii=False), _reading_error(found), line['id']))
            con.execute('DELETE FROM picks WHERE line_id = ?', (line['id'],))
    set_step(list_id, 'choosing')


def _reading_error(found: dict) -> str:
    if found['kind'] == 'artist' and not any(a['mbid'] for a in found['artists']):
        return found['artists'][0]['note'] if found['artists'] else 'No artist'
    if found['kind'] != 'artist' and not found['tags'] and not any(a['mbid'] for a in found['artists']):
        return 'Neither a tag nor an artist to start from'
    return ''


def choose_step(list_id: int) -> None:
    whole = get(list_id)
    if whole['mode'] == 'direct':
        set_step(list_id, 'videos')
        return
    used = choosing.Used()
    for line in whole['lines']:
        if line['chosen']:
            for pick in line['picks']:
                used.take(_song(pick))
    for n, line in enumerate(whole['lines'], 1):
        if line['chosen'] or line['error'] or not line['reading']:
            continue
        note(list_id, progress=f'Choosing songs for line {n} of {len(whole["lines"])}: {line["text"]}')
        pool = choosing.pool(line['reading'])
        picked, rest = choosing.pick(pool, line['reading']['years'], whole['songs_per_line'], used)
        with store.db() as con:
            con.execute('DELETE FROM picks WHERE line_id = ?', (line['id'],))
            for position, song in enumerate(picked):
                _insert(con, line['id'], position, song)
            con.execute('UPDATE lines SET chosen = 1, pool = ?, error = ? WHERE id = ?',
                        (json.dumps(rest, ensure_ascii=False),
                         '' if picked else 'ListenBrainz has no songs for this line', line['id']))
    set_step(list_id, 'videos')


def _youtube_auth(cfg: Config) -> tuple[str, str]:
    token = ''
    if google_auth.signed_in():
        try:
            token = google_auth.access_token(cfg)
        except google_auth.SignInNeeded:
            if not cfg.youtube_api_key:
                raise
    return cfg.youtube_api_key, token


def _direct_videos_step(cfg: Config, list_id: int, whole: dict) -> None:
    line = whole['lines'][0]
    meta = line['reading'] or {'kind': 'import', 'format': 'text', 'playlist_ids': []}
    playlist_ids = list(meta.get('playlist_ids') or [])
    key, token = _youtube_auth(cfg)

    if playlist_ids:
        if not key and not token:
            raise youtube.NoKey('Reading a YouTube playlist needs youtube_api_key or YouTube sign-in')
        existing_mbids = {p['recording_mbid'] for p in line['picks']}
        next_pos = max((p['position'] for p in line['picks']), default=-1) + 1
        for pid in playlist_ids:
            note(list_id, progress=f'Reading YouTube playlist {pid}')
            items = youtube.playlist_items(pid, key, token=token)
            if items is None:
                raise Failed(f'YouTube playlist {pid} was not found or is private')
            with store.db() as con:
                for item in items:
                    mbid = f'yt:{item["video_id"]}'
                    if mbid in existing_mbids:
                        continue
                    existing_mbids.add(mbid)
                    _insert(con, line['id'], next_pos, {
                        'mbid': mbid,
                        'title': item['title'] or item['video_id'],
                        'artist': item['channel'] or '',
                        'artist_mbids': [],
                        'artist_youtube': [],
                        'year': None,
                        'length_ms': None,
                        'why': 'From YouTube playlist',
                    })
                    next_pos += 1
        meta['playlist_ids'] = []
        with store.db() as con:
            con.execute('UPDATE lines SET reading = ? WHERE id = ?',
                        (json.dumps(meta, ensure_ascii=False), line['id']))
        whole = get(list_id)
        line = whole['lines'][0]

    known = videos.known()
    yt_picks = [p for p in line['picks'] if p['recording_mbid'].startswith('yt:')]
    unchecked_yt = [
        p for p in yt_picks
        if p['recording_mbid'] not in known or not known[p['recording_mbid']].get('yt_checked')
    ]
    if unchecked_yt:
        vid_ids = list(dict.fromkeys(p['recording_mbid'][3:] for p in unchecked_yt))
        if key or token:
            note(list_id, progress=f'Checking {len(vid_ids)} YouTube videos')
            details = youtube.videos(vid_ids, key, keep=False, token=token)
            for vid in vid_ids:
                detail = details.get(vid)
                if detail and detail['playable']:
                    videos.save(f'yt:{vid}', 'found', ['import'], vid, 'import', detail)
                else:
                    videos.save(f'yt:{vid}', 'gone', ['import'], vid, 'import', None)
            with store.db() as con:
                for pick in unchecked_yt:
                    vid = pick['recording_mbid'][3:]
                    detail = details.get(vid)
                    if not detail:
                        continue
                    new_title = detail['title'] if (not pick['title'] or pick['title'] == vid) else pick['title']
                    new_artist = detail['channel'] if not pick['artist'] else pick['artist']
                    new_len = (detail['duration_s'] * 1000) if (not pick['length_ms'] and detail['duration_s']) else pick['length_ms']
                    con.execute('UPDATE picks SET title = ?, artist = ?, length_ms = ? WHERE id = ?',
                                (new_title, new_artist, new_len, pick['id']))
        else:
            for vid in vid_ids:
                if f'yt:{vid}' not in known:
                    videos.save(f'yt:{vid}', 'found', ['import'], vid, 'import', None)

    text_picks = [
        p for p in line['picks']
        if p['recording_mbid'].startswith('text:') and p['recording_mbid'] not in known
    ]
    if text_picks:
        if not key and not token:
            raise youtube.NoKey('Finding videos for text lines needs youtube_api_key or YouTube sign-in')
        for n, pick in enumerate(text_picks, 1):
            note(list_id, progress=f'YouTube search, song {n} of {len(text_picks)}')
            query = f'{pick["artist"]} {videos.core_title(pick["title"])}'.strip()
            ids = youtube.search(query, key, token=token)
            found_details = youtube.videos(ids, key, token=token) if ids else {}
            song_obj = _song(pick)
            chosen = videos.best(song_obj, found_details, ids, set())
            if chosen:
                videos.save(pick['recording_mbid'], 'found', ['search'], chosen, 'search', found_details[chosen])
            else:
                videos.save(pick['recording_mbid'], 'none', ['search'])

    set_step(list_id, 'ready')


def videos_step(cfg: Config, list_id: int) -> None:
    whole = get(list_id)
    if whole['mode'] == 'direct':
        _direct_videos_step(cfg, list_id, whole)
        return
    songs = [_song(p) for line in whole['lines'] for p in line['picks']]
    videos.find(songs, cfg.youtube_api_key, progress=lambda text: note(list_id, progress=text))
    set_step(list_id, 'ready')


def _song(pick: dict) -> dict:
    return {'mbid': pick['recording_mbid'], 'title': pick['title'], 'artist': pick['artist'],
            'artist_mbids': pick['artist_mbids'], 'artist_youtube': pick['artist_youtube'], 'year': pick['year'],
            'length_ms': pick['length_ms'], 'why': pick['why']}


def _insert(con, line_id: int, position: int, song: dict) -> None:
    con.execute('INSERT INTO picks (line_id, position, recording_mbid, title, artist, artist_mbids, artist_youtube, '
                'year, length_ms, why) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (line_id, position, song['mbid'], song['title'], song['artist'], json.dumps(song['artist_mbids']),
                 json.dumps(song.get('artist_youtube', [])), song['year'], song['length_ms'], song['why']))


def _line_of(pick_id: int) -> tuple[dict, dict, dict]:
    with store.db() as con:
        row = con.execute('SELECT lines.list_id, picks.line_id FROM picks JOIN lines ON lines.id = picks.line_id '
                          'WHERE picks.id = ?', (pick_id,)).fetchone()
    if row is None:
        raise KeyError(pick_id)
    whole = get(row['list_id'])
    line = next(line for line in whole['lines'] if line['id'] == row['line_id'])
    pick = next(p for p in line['picks'] if p['id'] == pick_id)
    return whole, line, pick


def swap(pick_id: int) -> None:
    """Put the line's next candidate in the song's place."""
    whole, line, old = _line_of(pick_id)
    if whole['mode'] == 'direct':
        raise Failed('Imported song lists have no replacement candidates')
    used = choosing.Used()
    for other_line in whole['lines']:
        for pick in other_line['picks']:
            if pick['id'] != pick_id:
                used.take(_song(pick))
    used.take(_song(old))
    line_artists = {a for p in line['picks'] if p['id'] != pick_id for a in p['artist_mbids']}
    line_artists.update(old['artist_mbids'])
    with store.db() as con:
        pool = json.loads(con.execute('SELECT pool FROM lines WHERE id = ?', (line['id'],)).fetchone()['pool'])
    picked, rest = choosing.pick(pool, line['reading']['years'], 1, used, line_artists)
    if not picked:
        with store.db() as con:
            con.execute('UPDATE lines SET pool = ? WHERE id = ?', (json.dumps(rest, ensure_ascii=False), line['id']))
        raise Failed('This line has no more candidates')
    with store.db() as con:
        con.execute('DELETE FROM picks WHERE id = ?', (pick_id,))
        _insert(con, line['id'], old['position'], picked[0])
        con.execute('UPDATE lines SET pool = ? WHERE id = ?', (json.dumps(rest, ensure_ascii=False), line['id']))
    if whole['step'] == 'ready':
        set_step(whole['id'], 'videos')


def remove(pick_id: int) -> None:
    with store.db() as con:
        con.execute('UPDATE picks SET removed = 1 WHERE id = ?', (pick_id,))


def choose_artist(line_id: int, mbid: str) -> None:
    """Take the line's artist name as another of the MusicBrainz artists that share it, and choose again."""
    with store.db() as con:
        row = con.execute('SELECT list_id, reading FROM lines WHERE id = ?', (line_id,)).fetchone()
    if row is None:
        raise KeyError(line_id)
    found = json.loads(row['reading'])
    for artist in found['artists']:
        others = artist['others']
        chosen = next((o for o in others if o['mbid'] == mbid), None)
        if chosen:
            current = {k: artist[k] for k in ('mbid', 'name', 'comment')}
            artist['others'] = [current] + [o for o in others if o['mbid'] != mbid]
            artist.update(mbid=chosen['mbid'], name=chosen['name'], comment=chosen['comment'], note='')
            break
    else:
        raise Failed('That artist is not one of the names this line could mean')
    with store.db() as con:
        con.execute("UPDATE lines SET reading = ?, chosen = 0, pool = '[]', error = '' WHERE id = ?",
                    (json.dumps(found, ensure_ascii=False), line_id))
        con.execute('DELETE FROM picks WHERE line_id = ?', (line_id,))
    set_step(row['list_id'], 'choosing')
