"""Making the list's playlist in his YouTube account, only when he asks (SPEC.md, "What he sees", 4). Resumable:
a playlist past the day's budget gets its remaining songs when the budget is new, a song already in it is never
added twice, and a video YouTube refuses as missing is marked gone and skipped instead of halting the rest."""
from __future__ import annotations

from . import google_auth, importing, lists, net, store, youtube
from .config import Config

VISIBILITIES = ('unlisted', 'private', 'public')
TITLE_CHARS = 150
DESCRIPTION_BYTES = 5000


def clean(text: str) -> str:
    """YouTube refuses '<' and '>' in titles and descriptions."""
    return text.replace('<', '').replace('>', '')


def title(texts: list[str]) -> str:
    whole = clean('Like ' + ', '.join(t.strip() for t in texts))
    if len(whole) <= TITLE_CHARS:
        return whole
    cut = whole[:TITLE_CHARS - 3].rsplit(' ', 1)[0].rstrip(',')
    return cut + '...'


def direct_title(raw_title: str) -> str:
    whole = clean(raw_title.strip() or 'Imported songs')
    if len(whole) <= TITLE_CHARS:
        return whole
    cut = whole[:TITLE_CHARS - 3].rsplit(' ', 1)[0].rstrip(',')
    return (cut or whole[:TITLE_CHARS - 3]) + '...'


def description(sections: list[tuple[str, list[str]]], *, mode: str = 'similar') -> str:
    if mode == 'direct':
        return 'Songs imported with yt-playlist-maker.'
    out = ['Songs like each of these, chosen from ListenBrainz listening data and MusicBrainz (CC0).', '']
    for line, songs in sections:
        out.append(clean(f'{line}: ' + '; '.join(songs)))
    text = '\n'.join(out)
    while len(text.encode()) > DESCRIPTION_BYTES:
        text = text[:-100].rsplit('\n', 1)[0]
    return text


def start(list_id: int, visibility: str, *, custom_title: str = '', target_playlist: str = '') -> None:
    if visibility not in VISIBILITIES:
        raise ValueError(f'visibility must be one of {VISIBILITIES}')
    whole = lists.get(list_id)
    target_pid = ''
    if target_playlist and target_playlist.strip():
        target_pid = importing.extract_playlist_id(target_playlist) or ''
        if not target_pid:
            raise ValueError('Target playlist must be a YouTube playlist URL or playlist ID')
    elif whole.get('target_playlist_id'):
        target_pid = whole['target_playlist_id']

    if custom_title and custom_title.strip():
        playlist_title = direct_title(custom_title)
    elif whole.get('mode') == 'direct':
        playlist_title = direct_title(whole.get('title') or whole['lines'][0]['text'])
    else:
        playlist_title = title([line['text'] for line in whole['lines']])

    with store.db() as con:
        existing = con.execute('SELECT youtube_id FROM playlists WHERE list_id = ?', (list_id,)).fetchone()
        use_pid = (existing['youtube_id'] if existing and existing['youtube_id'] else None) or (target_pid or None)
        con.execute(
            'INSERT INTO playlists (list_id, step, visibility, title, youtube_id, created) VALUES (?, ?, ?, ?, ?, ?) '
            "ON CONFLICT (list_id) DO UPDATE SET step = 'saving', visibility = excluded.visibility, "
            "title = excluded.title, youtube_id = COALESCE(playlists.youtube_id, excluded.youtube_id), "
            "error = '', wait_until = NULL, wait_reason = ''",
            (list_id, 'saving', visibility, playlist_title, use_pid, store.stamp()),
        )


def _is_unavailable_video(exc: net.ServiceError) -> bool:
    if exc.status == 404:
        return True
    detail = exc.detail.lower()
    return exc.status == 400 and ('video' in detail and 'not found' in detail)


def save(cfg: Config, list_id: int) -> None:
    """Make the playlist, or add to it what it lacks. Raises net.OverBudget or google_auth.SignInNeeded."""
    whole = lists.get(list_id)
    playlist = whole['playlist']
    wanted = list(dict.fromkeys(p['video']['video_id'] for line in whole['lines'] for p in line['picks']
                                if p['video'] and p['video']['state'] == 'found' and p['video']['video_id']))
    token = google_auth.access_token(cfg)
    if not playlist['youtube_id']:
        sections = [(line['text'], [f'{p["artist"]} - {p["title"]}' for p in line['picks']]) for line in whole['lines']]
        made = youtube.create_playlist(
            token,
            playlist['title'],
            description(sections, mode=whole.get('mode', 'similar')),
            playlist['visibility'],
        )
        with store.db() as con:
            con.execute('UPDATE playlists SET youtube_id = ?, yt_checked = ? WHERE list_id = ?',
                        (made, store.stamp(), list_id))
        playlist['youtube_id'] = made
        present: list[str] = []
    else:
        present = youtube.playlist_videos(token, playlist['youtube_id'])
        if present is None:
            raise lists.Failed('YouTube no longer has this playlist: it was deleted there')
    have = set(present)
    length = len(present)
    skipped = int(playlist.get('skipped') or 0)
    for video in wanted:
        if video in have:
            continue
        try:
            youtube.add_to_playlist(token, playlist['youtube_id'], video, length)
        except net.ServiceError as exc:
            if _is_unavailable_video(exc):
                skipped += 1
                with store.db() as con:
                    con.execute("UPDATE videos SET state = 'gone' WHERE video_id = ?", (video,))
                    con.execute('UPDATE playlists SET skipped = ?, yt_checked = ? WHERE list_id = ?',
                                (skipped, store.stamp(), list_id))
                continue
            raise
        have.add(video)
        length += 1
        with store.db() as con:
            con.execute('UPDATE playlists SET added = ?, skipped = ?, yt_checked = ? WHERE list_id = ?',
                        (len(have & set(wanted)), skipped, store.stamp(), list_id))
    with store.db() as con:
        con.execute("UPDATE playlists SET step = 'saved', added = ?, skipped = ?, error = '', "
                    'wait_until = NULL, wait_reason = ? WHERE list_id = ?',
                    (len(have & set(wanted)), skipped, '', list_id))
