"""One thread that does the work, one list at a time, so tabs and reloads never add requests: a list's next step,
then a playlist to make, then, each hour, what YouTube's policies ask of kept data."""
from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

from . import google_auth, listenbrainz, lists, model, net, playlists, store, videos, youtube
from .config import Config

logger = logging.getLogger(__name__)

POLL_SECONDS = 2
AGAIN_MINUTES = 5
MAX_TRIES = 3
UPKEEP_SECONDS = 3600


class Worker(threading.Thread):
    def __init__(self, cfg: Config):
        super().__init__(name='worker', daemon=True)
        self.cfg = cfg
        self.stopped = threading.Event()
        self.upkept = 0.0

    def run(self) -> None:
        while not self.stopped.wait(POLL_SECONDS):
            try:
                self.tick()
            except Exception:
                logger.exception('The worker failed between lists')

    def tick(self) -> None:
        if time.monotonic() - self.upkept > UPKEEP_SECONDS:
            self.upkept = time.monotonic()
            upkeep(self.cfg)
        list_id = _due('lists', "step IN ('reading', 'choosing', 'videos')")
        if list_id:
            advance(self.cfg, list_id)
        playlist_id = _due('playlists', "step = 'saving'", key='list_id')
        if playlist_id:
            save(self.cfg, playlist_id)


def _due(table: str, where: str, key: str = 'id') -> int | None:
    with store.db() as con:
        row = con.execute(f"SELECT {key} FROM {table} WHERE {where} AND error = '' "
                          f'AND (wait_until IS NULL OR wait_until <= ?) ORDER BY {key} LIMIT 1',
                          (store.stamp(),)).fetchone()
    return row[key] if row else None


def advance(cfg: Config, list_id: int) -> None:
    whole = lists.get(list_id)
    step = whole['step']
    try:
        if step == 'reading':
            lists.read_step(cfg, list_id)
        elif step == 'choosing':
            lists.choose_step(list_id)
        else:
            lists.videos_step(cfg, list_id)
    except net.OverBudget as exc:
        lists.note(list_id, wait_until=store.iso(exc.until), wait_reason=str(exc), progress='')
    except model.Unavailable as exc:
        lists.note(list_id, wait_until=_later(), wait_reason=f'The language model cannot run now: {exc}', progress='')
    except (net.Unreachable, net.ServiceError) as exc:
        passing = isinstance(exc, net.Unreachable) or exc.status >= 500 or exc.status == 429
        tries = whole['tries'] + 1
        if passing and tries < MAX_TRIES:
            lists.note(list_id, wait_until=_later(), wait_reason=str(exc), tries=tries, progress='')
        else:
            lists.note(list_id, error=str(exc), progress='')
    except (lists.Failed, model.BadAnswer, youtube.NoKey, listenbrainz.NoToken) as exc:
        lists.note(list_id, error=str(exc), progress='')
    except Exception as exc:
        logger.exception('List %s failed at %s', list_id, step)
        lists.note(list_id, error=f'{type(exc).__name__}: {exc}', progress='')


def save(cfg: Config, list_id: int) -> None:
    try:
        playlists.save(cfg, list_id)
    except net.OverBudget as exc:
        _playlist(list_id, wait_until=store.iso(exc.until), wait_reason=str(exc))
    except (net.Unreachable, net.ServiceError) as exc:
        passing = isinstance(exc, net.Unreachable) or exc.status >= 500
        if passing:
            _playlist(list_id, wait_until=_later(), wait_reason=str(exc))
        else:
            _playlist(list_id, error=str(exc))
    except (google_auth.SignInNeeded, lists.Failed) as exc:
        _playlist(list_id, error=str(exc))
    except Exception as exc:
        logger.exception('The playlist of list %s failed', list_id)
        _playlist(list_id, error=f'{type(exc).__name__}: {exc}')


def _playlist(list_id: int, **fields) -> None:
    names = ', '.join(f'{k} = ?' for k in fields)
    with store.db() as con:
        con.execute(f'UPDATE playlists SET {names} WHERE list_id = ?', (*fields.values(), list_id))


def _later() -> str:
    return store.iso(net.now() + timedelta(minutes=AGAIN_MINUTES))


def upkeep(cfg: Config) -> dict:
    """YouTube's answers are kept 30 days at most; its facts about videos and playlists are checked again before
    then, or deleted (SPEC.md, "Kept answers")."""
    done = {'answers_deleted': store.forget_answers('youtube', net.now() - timedelta(days=videos.KEEP_DAYS))}
    done['videos'] = videos.refresh(cfg.youtube_api_key)
    done['playlists'] = refresh_playlists(cfg)
    return done


def refresh_playlists(cfg: Config) -> dict:
    cutoff = store.iso(net.now() - timedelta(days=videos.CHECK_AFTER_DAYS))
    last_day = store.iso(net.now() - timedelta(days=videos.KEEP_DAYS))
    with store.db() as con:
        rows = [dict(r) for r in con.execute('SELECT * FROM playlists WHERE youtube_id IS NOT NULL '
                                             'AND yt_checked < ?', (cutoff,))]
    checked = forgotten = 0
    for row in rows:
        try:
            token = google_auth.access_token(cfg) if google_auth.signed_in() else ''
            if not token and (row['visibility'] == 'private' or not cfg.youtube_api_key):
                raise google_auth.SignInNeeded('cannot see it')
            exists = youtube.playlist_exists(row['youtube_id'], cfg.youtube_api_key, token)
        except (google_auth.SignInNeeded, net.OverBudget, net.ServiceError, net.Unreachable, youtube.NoKey):
            exists = None
        with store.db() as con:
            if exists:
                checked += 1
                con.execute('UPDATE playlists SET yt_checked = ? WHERE list_id = ?', (store.stamp(), row['list_id']))
            elif exists is None and row['yt_checked'] >= last_day:
                continue
            else:
                forgotten += 1
                why = ('YouTube no longer has this playlist' if exists is False else
                       'Its link is not kept here past 30 days without a check; the playlist is in your YouTube '
                       'library')
                con.execute("UPDATE playlists SET youtube_id = NULL, yt_checked = NULL, step = 'failed', error = ? "
                            'WHERE list_id = ?', (why, row['list_id']))
    return {'checked': checked, 'forgotten': forgotten}


def forget_youtube() -> dict:
    """What he asks to delete with "Sign out and delete YouTube data" (Developer Policies III.E.4.g)."""
    with store.db() as con:
        found = con.execute("DELETE FROM videos WHERE source IN ('channel', 'search')").rowcount
        con.execute('UPDATE videos SET yt_title = NULL, yt_channel = NULL, yt_duration_s = NULL, yt_checked = NULL')
        links = con.execute('UPDATE playlists SET youtube_id = NULL, yt_checked = NULL WHERE youtube_id IS NOT NULL'
                            ).rowcount
    return {'answers': store.forget_answers('youtube'), 'videos': found, 'playlist_links': links}
