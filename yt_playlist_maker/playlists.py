"""Making the list's playlist in his YouTube account, only when he asks (SPEC.md, "What he sees", 4). Resumable:
a playlist past the day's budget gets its remaining songs when the budget is new, and a song already in it is
never added twice."""
from __future__ import annotations

from . import google_auth, lists, net, store, youtube
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


def description(sections: list[tuple[str, list[str]]]) -> str:
    out = ['Songs like each of these, chosen from ListenBrainz listening data and MusicBrainz (CC0).', '']
    for line, songs in sections:
        out.append(clean(f'{line}: ' + '; '.join(songs)))
    text = '\n'.join(out)
    while len(text.encode()) > DESCRIPTION_BYTES:
        text = text[:-100].rsplit('\n', 1)[0]
    return text


def start(list_id: int, visibility: str) -> None:
    if visibility not in VISIBILITIES:
        raise ValueError(f'visibility must be one of {VISIBILITIES}')
    whole = lists.get(list_id)
    with store.db() as con:
        con.execute(
            'INSERT INTO playlists (list_id, step, visibility, title, created) VALUES (?, ?, ?, ?, ?) '
            "ON CONFLICT (list_id) DO UPDATE SET step = 'saving', error = '', wait_until = NULL, wait_reason = ''",
            (list_id, 'saving', visibility, title([line['text'] for line in whole['lines']]), store.stamp()))


def save(cfg: Config, list_id: int) -> None:
    """Make the playlist, or add to it what it lacks. Raises net.OverBudget or google_auth.SignInNeeded."""
    whole = lists.get(list_id)
    playlist = whole['playlist']
    wanted = list(dict.fromkeys(p['video']['video_id'] for line in whole['lines'] for p in line['picks']
                                if p['video'] and p['video']['state'] == 'found'))
    token = google_auth.access_token(cfg)
    if not playlist['youtube_id']:
        sections = [(line['text'], [f'{p["artist"]} - {p["title"]}' for p in line['picks']]) for line in whole['lines']]
        made = youtube.create_playlist(token, playlist['title'], description(sections), playlist['visibility'])
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
    for video in wanted:
        if video in have:
            continue
        youtube.add_to_playlist(token, playlist['youtube_id'], video, length)
        have.add(video)
        length += 1
        with store.db() as con:
            con.execute('UPDATE playlists SET added = ?, yt_checked = ? WHERE list_id = ?',
                        (len(have & set(wanted)), store.stamp(), list_id))
    with store.db() as con:
        con.execute("UPDATE playlists SET step = 'saved', added = ?, error = '', wait_until = NULL, wait_reason = '' "
                    'WHERE list_id = ?', (len(have & set(wanted)), list_id))
