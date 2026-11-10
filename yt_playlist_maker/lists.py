"""His lists: what he typed, how each line was read, the songs picked and their videos. The worker moves a list
through its steps (reading, choosing, videos); a step that fails keeps its step and says why in error."""
from __future__ import annotations

import json

from . import choosing, reading, store, videos, youtube
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
        list_id = con.execute('INSERT INTO lists (created, text, songs_per_line, step) VALUES (?, ?, ?, ?)',
                              (store.stamp(), text, songs_per_line, 'reading')).lastrowid
        con.executemany('INSERT INTO lines (list_id, position, text) VALUES (?, ?, ?)',
                        [(list_id, n, line) for n, line in enumerate(lines)])
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
            'SELECT lists.id, lists.created, lists.step, lists.error, playlists.youtube_id, playlists.step AS saved, '
            '(SELECT group_concat(text, ", ") FROM (SELECT text FROM lines WHERE list_id = lists.id '
            'ORDER BY position)) AS lines FROM lists LEFT JOIN playlists ON playlists.list_id = lists.id '
            'ORDER BY lists.id DESC LIMIT 100').fetchall()
    out = []
    for r in rows:
        item = dict(r)
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


def videos_step(cfg: Config, list_id: int) -> None:
    whole = get(list_id)
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
