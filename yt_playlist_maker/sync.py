"""Encrypted two-way library sync between voice-wei (phone browser) and a YouTube playlist on this computer.

Crypto protocol (byte-identical to voice-wei/player-library-sync.js):
- HKDF-SHA256 (RFC 5869) from UTF-8(sync_key.strip()), salt = b'voice-wei-sync-v1':
  - info = b'auth'    -> 32 bytes -> 64 lowercase hex chars sent in X-Voice-Wei-Sync-Token
                         (the server stores only SHA-256(auth_token_hex) in shared/sync-auth.json)
  - info = b'encrypt' -> 32 bytes -> AES-256-GCM key that never leaves client devices
- Wire format: b'VWS1' || iv (12 bytes) || AES-256-GCM(UTF-8 JSON, aad=b'VWS1').

Safe two-way YouTube rule:
- Favorites starred in voice-wei are added to the YouTube playlist (within YouTube's daily budget).
- Songs added to that YouTube playlist on the computer are merged into voice-wei favorites when
  added_at_ms > removedFavorites[videoId].
- Removing a song from the YouTube playlist NEVER auto-unstars it in voice-wei; explicit unstars inside
  voice-wei propagate via removedFavorites tombstones.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import threading
import time
from datetime import timedelta
from urllib.parse import urlparse

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import config, google_auth, net, playlists, store, videos, youtube
from .config import Config

SYNC_MAGIC = b'VWS1'
SYNC_SALT = b'voice-wei-sync-v1'
SYNC_INFO_AUTH = b'auth'
SYNC_INFO_ENCRYPT = b'encrypt'
SYNC_MIN_KEY_LENGTH = 16
SYNC_MAX_BYTES = 5 * 1024 * 1024
SYNC_FORMAT = 'voice-wei-library'
SYNC_VERSION = 1
YOUTUBE_PULL_MINUTES = 60

_run_lock = threading.Lock()


class SyncError(RuntimeError):
    pass


def configured(cfg: Config) -> bool:
    return bool(cfg.sync_url.strip() and cfg.sync_key.strip())


def derive_keys(sync_key: str) -> tuple[str, bytes]:
    """Derive (auth_token_hex, aes256_gcm_key) from a shared sync key via HKDF-SHA256."""
    cleaned = str(sync_key or '').strip()
    if len(cleaned) < SYNC_MIN_KEY_LENGTH:
        raise ValueError(f'Sync key must be at least {SYNC_MIN_KEY_LENGTH} characters')
    ikm = cleaned.encode('utf-8')
    auth_bytes = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=SYNC_SALT,
        info=SYNC_INFO_AUTH,
    ).derive(ikm)
    enc_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=SYNC_SALT,
        info=SYNC_INFO_ENCRYPT,
    ).derive(ikm)
    return auth_bytes.hex(), enc_key


def token_sha256(auth_token_hex: str) -> str:
    return hashlib.sha256(auth_token_hex.encode('utf-8')).hexdigest()


def encrypt_snapshot(snapshot: dict, enc_key: bytes, *, iv: bytes | None = None) -> bytes:
    nonce = os.urandom(12) if iv is None else iv
    if len(nonce) != 12:
        raise ValueError('AES-GCM IV must be 12 bytes')
    plaintext = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    ciphertext = AESGCM(enc_key).encrypt(nonce, plaintext, SYNC_MAGIC)
    blob = SYNC_MAGIC + nonce + ciphertext
    if len(blob) > SYNC_MAX_BYTES:
        raise SyncError(f'Encrypted snapshot exceeds {SYNC_MAX_BYTES} bytes')
    return blob


def decrypt_snapshot(blob: bytes, enc_key: bytes) -> dict:
    if len(blob) < 4 + 12 + 16 or blob[:4] != SYNC_MAGIC:
        raise SyncError('Encrypted sync blob has invalid header')
    nonce = blob[4:16]
    ciphertext = blob[16:]
    try:
        plaintext = AESGCM(enc_key).decrypt(nonce, ciphertext, SYNC_MAGIC)
    except InvalidTag as exc:
        raise SyncError('Could not decrypt sync blob (wrong sync key or corrupted data)') from exc
    try:
        parsed = json.loads(plaintext.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SyncError(f'Decrypted sync payload is not valid JSON: {exc}') from exc
    if not isinstance(parsed, dict) or parsed.get('format') != SYNC_FORMAT or parsed.get('version') != SYNC_VERSION:
        raise SyncError('Decrypted sync payload is not a voice-wei-library v1 snapshot')
    return parsed


def empty_snapshot() -> dict:
    return {
        'format': SYNC_FORMAT,
        'version': SYNC_VERSION,
        'exportedAt': int(time.time() * 1000),
        'scope': 'favorites',
        'title': 'Favorite Songs',
        'favorites': {},
        'removedFavorites': {},
        'listening': {},
        'lyricOffsets': {},
        'songReports': {},
        'songs': [],
    }


def _int_ts(value) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return 0


def _merge_favorite_record(base: dict | None, incoming: dict | None, video_id: str) -> dict:
    a = dict(base) if isinstance(base, dict) else {}
    b = dict(incoming) if isinstance(incoming, dict) else {}
    a_fav = _int_ts(a.get('favoritedAt'))
    b_fav = _int_ts(b.get('favoritedAt'))
    if a_fav and b_fav:
        fav_at = min(a_fav, b_fav)
    else:
        fav_at = a_fav or b_fav or int(time.time() * 1000)
    a_upd = max(a_fav, _int_ts(a.get('updatedAt')))
    b_upd = max(b_fav, _int_ts(b.get('updatedAt')))
    newer, older = (b, a) if b_upd >= a_upd else (a, b)
    merged = {**older}
    for key, val in newer.items():
        if val not in (None, ''):
            merged[key] = val
    merged['videoId'] = video_id
    merged['favoritedAt'] = fav_at
    merged['updatedAt'] = max(a_upd, b_upd, fav_at)
    return merged


def merge_snapshots(local: dict | None, incoming: dict | None, *, apply_tombstones: bool = True) -> dict:
    """CRDT merge matching voice-wei/player-library-sync.js mergeLibrarySnapshots()."""
    a = local if isinstance(local, dict) else empty_snapshot()
    b = incoming if isinstance(incoming, dict) else empty_snapshot()

    a_favs = a.get('favorites') if isinstance(a.get('favorites'), dict) else {}
    b_favs = b.get('favorites') if isinstance(b.get('favorites'), dict) else {}
    a_rem = a.get('removedFavorites') if isinstance(a.get('removedFavorites'), dict) else {}
    b_rem = b.get('removedFavorites') if isinstance(b.get('removedFavorites'), dict) else {}

    tombstones: dict[str, int] = {}
    for src in (a_rem, b_rem):
        for vid, ts in src.items():
            t = _int_ts(ts)
            if t > tombstones.get(vid, 0):
                tombstones[vid] = t

    all_ids = set(a_favs) | set(b_favs)
    favorites: dict[str, dict] = {}
    for vid in all_ids:
        rec_a = a_favs.get(vid) if isinstance(a_favs.get(vid), dict) else None
        rec_b = b_favs.get(vid) if isinstance(b_favs.get(vid), dict) else None
        merged = _merge_favorite_record(rec_a, rec_b, vid)
        fav_ts = max(_int_ts(merged.get('favoritedAt')), _int_ts(merged.get('updatedAt')))
        rem_ts = tombstones.get(vid, 0)
        if apply_tombstones and rem_ts > fav_ts:
            continue
        favorites[vid] = merged
        if rem_ts and fav_ts >= rem_ts:
            tombstones.pop(vid, None)

    listening: dict[str, dict] = {}
    for src in (a.get('listening'), b.get('listening')):
        if not isinstance(src, dict):
            continue
        for vid, rec in src.items():
            if not isinstance(rec, dict):
                continue
            prev = listening.get(vid)
            if not prev:
                listening[vid] = dict(rec)
                continue
            first_vals = [x for x in (prev.get('firstPlayedAt'), rec.get('firstPlayedAt')) if x]
            last_vals = [x for x in (prev.get('lastPlayedAt'), rec.get('lastPlayedAt')) if x]
            listening[vid] = {
                **prev,
                **rec,
                'videoId': vid,
                'plays': max(int(prev.get('plays') or 0), int(rec.get('plays') or 0)),
                'secondsHeard': max(float(prev.get('secondsHeard') or 0), float(rec.get('secondsHeard') or 0)),
                'firstPlayedAt': min(first_vals) if first_vals else '',
                'lastPlayedAt': max(last_vals) if last_vals else '',
            }

    lyric_offsets: dict[str, float] = {}
    for src in (a.get('lyricOffsets'), b.get('lyricOffsets')):
        if not isinstance(src, dict):
            continue
        for vid, offset in src.items():
            if isinstance(offset, (int, float)) and not isinstance(offset, bool):
                lyric_offsets[vid] = float(offset)

    song_reports: dict[str, dict] = {}
    for src in (a.get('songReports'), b.get('songReports')):
        if not isinstance(src, dict):
            continue
        for vid, rep in src.items():
            if not isinstance(rep, dict):
                continue
            prev = song_reports.get(vid)
            if not prev or str(rep.get('savedAt') or '') >= str(prev.get('savedAt') or ''):
                song_reports[vid] = dict(rep)

    # Preserve song order: incoming songs first, then local songs, then any favorite not listed in songs.
    songs_by_id: dict[str, dict] = {}
    ordered_ids: list[str] = []
    for seq in (b.get('songs'), a.get('songs')):
        if not isinstance(seq, list):
            continue
        for item in seq:
            if not isinstance(item, dict):
                continue
            vid = str(item.get('videoId') or '').strip()
            if not vid:
                continue
            if vid not in songs_by_id:
                songs_by_id[vid] = dict(item)
                ordered_ids.append(vid)
            else:
                for key, val in item.items():
                    if not songs_by_id[vid].get(key) and val not in (None, ''):
                        songs_by_id[vid][key] = val

    for vid, fav in sorted(favorites.items(), key=lambda kv: _int_ts(kv[1].get('favoritedAt'))):
        if vid not in songs_by_id:
            songs_by_id[vid] = dict(fav)
            ordered_ids.append(vid)
        else:
            for key in ('name', 'artist', 'title', 'channelTitle', 'year', 'album', 'duration', 'durationSeconds'):
                if fav.get(key) and not songs_by_id[vid].get(key):
                    songs_by_id[vid][key] = fav[key]

    merged_songs = [songs_by_id[vid] for vid in ordered_ids if vid in favorites]
    return {
        'format': SYNC_FORMAT,
        'version': SYNC_VERSION,
        'exportedAt': max(_int_ts(a.get('exportedAt')), _int_ts(b.get('exportedAt')), int(time.time() * 1000)),
        'scope': 'favorites',
        'title': str(b.get('title') or a.get('title') or 'Favorite Songs'),
        'favorites': favorites,
        'removedFavorites': tombstones,
        'listening': listening,
        'lyricOffsets': lyric_offsets,
        'songReports': song_reports,
        'songs': merged_songs,
    }


def endpoint_url(sync_url: str, op: str) -> str:
    base = sync_url.strip().split('?', 1)[0]
    if base.endswith('/'):
        base = base + 'proxy.php'
    elif not base.endswith('.php'):
        base = base + '/proxy.php'
    return f'{base}?sync={op}'


def _normalize_etag(value: str | None) -> str:
    text = str(value or '').strip()
    if text.startswith('W/'):
        text = text[2:].strip()
    return text.strip('" \t\r\n')


def _load_state() -> dict:
    with store.db() as con:
        row = con.execute('SELECT * FROM sync_state WHERE id = 1').fetchone()
    if row is not None:
        return dict(row)
    return {
        'id': 1,
        'etag': '',
        'last_pull': None,
        'last_push': None,
        'last_error': '',
        'wait_until': None,
        'wait_reason': '',
        'playlist_id': '',
        'playlist_title': '',
        'playlist_visibility': 'unlisted',
        'yt_checked': None,
        'favorites_count': 0,
        'synced_count': 0,
        'pending_count': 0,
        'unavailable_count': 0,
        'pulled_from_youtube': 0,
        'synced_ids_json': '[]',
        'snapshot_json': '',
    }


def _save_state(state: dict) -> None:
    cols = (
        'id', 'etag', 'last_pull', 'last_push', 'last_error', 'wait_until', 'wait_reason',
        'playlist_id', 'playlist_title', 'playlist_visibility', 'yt_checked',
        'favorites_count', 'synced_count', 'pending_count', 'unavailable_count',
        'pulled_from_youtube', 'synced_ids_json', 'snapshot_json',
    )
    vals = [state.get(c) for c in cols]
    placeholders = ', '.join('?' for _ in cols)
    updates = ', '.join(f'{c} = excluded.{c}' for c in cols if c != 'id')
    with store.db() as con:
        con.execute(
            f'INSERT INTO sync_state ({", ".join(cols)}) VALUES ({placeholders}) '
            f'ON CONFLICT (id) DO UPDATE SET {updates}',
            vals,
        )


def _pull_remote(url: str, auth_token: str, enc_key: bytes, prev_etag: str) -> tuple[str, dict | None, bool]:
    """Return (etag, snapshot_or_none, changed)."""
    headers = {'X-Voice-Wei-Sync-Token': auth_token}
    if prev_etag:
        headers['If-None-Match'] = f'"{prev_etag}"'
    reply = net.raw('sync', 'GET', url, headers=headers)
    if reply.status_code == 304:
        return prev_etag, None, False
    if reply.status_code == 204:
        return 'empty', None, prev_etag != 'empty'
    if not reply.ok:
        raise net.ServiceError('sync', reply.status_code, net._detail(reply))
    etag = _normalize_etag(reply.headers.get('ETag') or reply.headers.get('etag'))
    snapshot = decrypt_snapshot(reply.content, enc_key)
    return etag, snapshot, True


def _push_remote(url: str, auth_token: str, enc_key: bytes, snapshot: dict, expected_etag: str) -> tuple[str, dict]:
    """Encrypt and POST snapshot with If-Match optimistic concurrency (retrying on 409)."""
    current = snapshot
    etag = expected_etag or 'empty'
    for _ in range(3):
        blob = encrypt_snapshot(current, enc_key)
        headers = {
            'X-Voice-Wei-Sync-Token': auth_token,
            'Content-Type': 'application/octet-stream',
            'If-Match': f'"{etag}"',
        }
        reply = net.raw('sync', 'POST', url, data=blob, headers=headers)
        if reply.status_code == 409:
            new_etag, remote_snap, _ = _pull_remote(url, auth_token, enc_key, '')
            current = merge_snapshots(current, remote_snap, apply_tombstones=True)
            etag = new_etag or 'empty'
            continue
        if not reply.ok:
            raise net.ServiceError('sync', reply.status_code, net._detail(reply))
        new_etag = _normalize_etag(reply.headers.get('ETag') or reply.headers.get('etag'))
        if not new_etag:
            try:
                new_etag = _normalize_etag(reply.json().get('etag'))
            except ValueError:
                new_etag = etag
        return new_etag, current
    raise SyncError('Sync conflict persisted after 3 retries')


def _ordered_favorite_ids(snapshot: dict) -> list[str]:
    favs = snapshot.get('favorites') if isinstance(snapshot.get('favorites'), dict) else {}
    out: list[str] = []
    seen: set[str] = set()
    for item in snapshot.get('songs') or []:
        if not isinstance(item, dict):
            continue
        vid = str(item.get('videoId') or '').strip()
        if vid and vid in favs and vid not in seen:
            seen.add(vid)
            out.append(vid)
    for vid, rec in sorted(favs.items(), key=lambda kv: _int_ts((kv[1] or {}).get('favoritedAt'))):
        if vid not in seen:
            seen.add(vid)
            out.append(vid)
    return out


def _video_states(video_ids: list[str]) -> dict[str, dict]:
    if not video_ids:
        return {}
    keys = [f'yt:{vid}' for vid in video_ids]
    out: dict[str, dict] = {}
    with store.db() as con:
        for start in range(0, len(keys), 500):
            chunk = keys[start:start + 500]
            marks = ', '.join('?' for _ in chunk)
            for row in con.execute(f'SELECT * FROM videos WHERE recording_mbid IN ({marks})', chunk):
                out[row['recording_mbid'][3:]] = dict(row)
    return out


def _is_not_found_video(exc: net.ServiceError) -> bool:
    if exc.status == 404:
        return True
    detail = exc.detail.lower()
    return exc.status == 400 and ('video' in detail and 'not found' in detail)


def run(cfg: Config, *, force: bool = False) -> dict:
    """Run one encrypted pull + safe two-way YouTube sync + conditional encrypted push."""
    if not configured(cfg):
        return status(cfg)

    with _run_lock:
        state = _load_state()
        auth_token, enc_key = derive_keys(cfg.sync_key)
        url = endpoint_url(cfg.sync_url, 'library')
        local_snap = json.loads(state['snapshot_json']) if state['snapshot_json'] else None
        prev_etag = state['etag'] or ''

        try:
            remote_etag, remote_snap, remote_changed = _pull_remote(url, auth_token, enc_key, prev_etag)
            now_stamp = store.stamp()
            state['last_pull'] = now_stamp
            state['last_error'] = ''

            if remote_snap is not None:
                merged = merge_snapshots(local_snap, remote_snap, apply_tombstones=True)
            elif local_snap is not None:
                merged = local_snap
            else:
                merged = empty_snapshot()

            state['etag'] = remote_etag
            dirty_remote = False

            target_pid = cfg.sync_playlist_id.strip() or state['playlist_id'] or ''
            yt_have: set[str] = set(json.loads(state['synced_ids_json'] or '[]'))
            pulled_from_yt = 0
            wait_until = state['wait_until']
            wait_reason = state['wait_reason']

            if wait_until and store.at(wait_until) <= net.now():
                wait_until = None
                wait_reason = ''

            yt_stale = (
                not state['yt_checked']
                or store.at(state['yt_checked']) <= net.now() - timedelta(minutes=YOUTUBE_PULL_MINUTES)
            )
            should_touch_youtube = (
                google_auth.signed_in()
                and (
                    force
                    or remote_changed
                    or (state['pending_count'] > 0 and not wait_until)
                    or yt_stale
                )
            )

            if should_touch_youtube:
                try:
                    token = google_auth.access_token(cfg)
                    if target_pid:
                        yt_items = youtube.playlist_items(target_pid, token=token)
                        if yt_items is None:
                            if cfg.sync_playlist_id.strip():
                                raise SyncError('YouTube no longer has sync_playlist_id: it was deleted there')
                            target_pid = ''
                            yt_items = []
                        else:
                            state['yt_checked'] = store.stamp()
                    else:
                        yt_items = []

                    yt_have = {item['video_id'] for item in yt_items}
                    yt_length = len(yt_items)

                    # Pull PC -> Phone (safe: additions only, never auto-unstarring voice-wei favorites).
                    now_ms = int(time.time() * 1000)
                    favs = merged.setdefault('favorites', {})
                    tombstones = merged.setdefault('removedFavorites', {})
                    songs_list = merged.setdefault('songs', [])
                    songs_index = {str(s.get('videoId') or ''): s for s in songs_list if isinstance(s, dict)}
                    for item in yt_items:
                        vid = item['video_id']
                        if vid in favs:
                            continue
                        removed_at = _int_ts(tombstones.get(vid))
                        added_at = _int_ts(item.get('added_at_ms'))
                        if removed_at and added_at <= removed_at:
                            continue
                        rec = {
                            'videoId': vid,
                            'name': item['title'] or vid,
                            'artist': item['channel'] or '',
                            'title': item['title'] or vid,
                            'channelTitle': item['channel'] or '',
                            'favoritedAt': added_at or now_ms,
                            'updatedAt': now_ms,
                            'sourceKind': 'youtube-sync',
                            'sourceLabel': 'YouTube playlist sync',
                        }
                        favs[vid] = rec
                        tombstones.pop(vid, None)
                        if vid not in songs_index:
                            songs_list.append(dict(rec))
                            songs_index[vid] = songs_list[-1]
                        pulled_from_yt += 1
                        dirty_remote = True

                    # Push Phone -> PC: check unchecked missing favorites in batches of 50, then insert playable ones.
                    fav_ids = _ordered_favorite_ids(merged)
                    missing = [vid for vid in fav_ids if vid not in yt_have]
                    known_videos = _video_states(missing)
                    unchecked = [vid for vid in missing if vid not in known_videos or not known_videos[vid]['yt_checked']]
                    if unchecked:
                        details = youtube.videos(unchecked, cfg.youtube_api_key, keep=False, token=token)
                        for vid in unchecked:
                            detail = details.get(vid)
                            if detail and detail['playable']:
                                videos.save(f'yt:{vid}', 'found', ['sync'], vid, 'sync', detail)
                            else:
                                videos.save(f'yt:{vid}', 'gone', ['sync'], vid, 'sync', None)
                        known_videos = _video_states(missing)

                    playable_missing = [
                        vid for vid in missing
                        if known_videos.get(vid, {}).get('state') == 'found'
                    ]

                    if playable_missing and not target_pid:
                        title = playlists.clean(cfg.sync_title or merged.get('title') or 'Favorite Songs')[:playlists.TITLE_CHARS]
                        desc = 'Synced favorite songs maintained by yt-playlist-maker.'
                        target_pid = youtube.create_playlist(token, title, desc, cfg.sync_visibility)
                        state['playlist_id'] = target_pid
                        state['playlist_title'] = title
                        state['playlist_visibility'] = cfg.sync_visibility
                        state['yt_checked'] = store.stamp()
                        yt_length = 0

                    for vid in playable_missing:
                        try:
                            youtube.add_to_playlist(token, target_pid, vid, yt_length)
                        except net.ServiceError as exc:
                            if _is_not_found_video(exc):
                                videos.save(f'yt:{vid}', 'gone', ['sync'], vid, 'sync', None)
                                continue
                            raise
                        yt_have.add(vid)
                        yt_length += 1
                        state['yt_checked'] = store.stamp()

                    wait_until = None
                    wait_reason = ''
                except net.OverBudget as exc:
                    wait_until = store.iso(exc.until)
                    wait_reason = str(exc)
                except google_auth.SignInNeeded as exc:
                    state['last_error'] = str(exc)

            if dirty_remote:
                merged['exportedAt'] = int(time.time() * 1000)
                new_etag, merged = _push_remote(url, auth_token, enc_key, merged, state['etag'])
                state['etag'] = new_etag
                state['last_push'] = store.stamp()

            # Recompute accurate counts for UI.
            fav_ids = _ordered_favorite_ids(merged)
            known_all = _video_states(fav_ids)
            synced_count = sum(1 for vid in fav_ids if vid in yt_have)
            unavailable_count = sum(
                1 for vid in fav_ids
                if vid not in yt_have and known_all.get(vid, {}).get('state') == 'gone'
            )
            pending_count = max(0, len(fav_ids) - synced_count - unavailable_count)

            state['playlist_id'] = target_pid
            if not state['playlist_title']:
                state['playlist_title'] = cfg.sync_title or merged.get('title') or 'Favorite Songs'
            state['playlist_visibility'] = cfg.sync_visibility
            state['wait_until'] = wait_until
            state['wait_reason'] = wait_reason
            state['favorites_count'] = len(fav_ids)
            state['synced_count'] = synced_count
            state['pending_count'] = pending_count
            state['unavailable_count'] = unavailable_count
            state['pulled_from_youtube'] = pulled_from_yt
            state['synced_ids_json'] = json.dumps(sorted(yt_have))
            state['snapshot_json'] = json.dumps(merged, ensure_ascii=False)
            _save_state(state)
        except (SyncError, net.ServiceError, net.Unreachable, ValueError) as exc:
            state['last_error'] = str(exc)
            _save_state(state)
        return status(cfg)


def status(cfg: Config) -> dict:
    state = _load_state()
    pid = cfg.sync_playlist_id.strip() or state['playlist_id'] or ''
    host = (urlparse(cfg.sync_url.strip()).hostname or '') if cfg.sync_url.strip() else ''
    return {
        'configured': configured(cfg),
        'partial': bool(cfg.sync_url.strip()) != bool(cfg.sync_key.strip()),
        'url_host': host,
        'sync_minutes': cfg.sync_minutes,
        'playlist_id': pid,
        'playlist_url': youtube.playlist_url(pid) if pid else '',
        'playlist_title': state['playlist_title'] or cfg.sync_title or 'Favorite Songs',
        'playlist_visibility': state['playlist_visibility'] or cfg.sync_visibility,
        'favorites_count': state['favorites_count'],
        'synced_count': state['synced_count'],
        'pending_count': state['pending_count'],
        'unavailable_count': state['unavailable_count'],
        'pulled_from_youtube': state['pulled_from_youtube'],
        'last_pull': state['last_pull'],
        'last_push': state['last_push'],
        'last_error': state['last_error'],
        'wait_until': state['wait_until'],
        'wait_reason': state['wait_reason'],
    }


def songs(cfg: Config) -> list[dict]:
    state = _load_state()
    if not state['snapshot_json']:
        return []
    snapshot = json.loads(state['snapshot_json'])
    favs = snapshot.get('favorites') if isinstance(snapshot.get('favorites'), dict) else {}
    yt_have = set(json.loads(state['synced_ids_json'] or '[]'))
    fav_ids = _ordered_favorite_ids(snapshot)
    known_all = _video_states(fav_ids)
    out: list[dict] = []
    for vid in fav_ids:
        rec = favs.get(vid) or {}
        if vid in yt_have:
            row_state = 'synced'
        elif known_all.get(vid, {}).get('state') == 'gone':
            row_state = 'unavailable'
        else:
            row_state = 'pending'
        out.append({
            'video_id': vid,
            'title': str(rec.get('name') or rec.get('title') or vid),
            'artist': str(rec.get('artist') or rec.get('channelTitle') or ''),
            'year': str(rec.get('year') or ''),
            'favorited_at': _int_ts(rec.get('favoritedAt')),
            'state': row_state,
            'url': f'https://www.youtube.com/watch?v={vid}',
        })
    return out


def refresh_playlist(cfg: Config) -> dict:
    """Keep the synced playlist link compliant with YouTube's 30-day cache rule."""
    state = _load_state()
    pid = state['playlist_id']
    if not pid or not state['yt_checked']:
        return {'checked': 0, 'forgotten': 0}
    cutoff = store.iso(net.now() - timedelta(days=videos.CHECK_AFTER_DAYS))
    last_day = store.iso(net.now() - timedelta(days=videos.KEEP_DAYS))
    if state['yt_checked'] >= cutoff:
        return {'checked': 0, 'forgotten': 0}
    try:
        token = google_auth.access_token(cfg) if google_auth.signed_in() else ''
        if not token and (state['playlist_visibility'] == 'private' or not cfg.youtube_api_key):
            raise google_auth.SignInNeeded('cannot see it')
        exists = youtube.playlist_exists(pid, cfg.youtube_api_key, token)
    except (google_auth.SignInNeeded, net.OverBudget, net.ServiceError, net.Unreachable, youtube.NoKey):
        exists = None
    if exists:
        state['yt_checked'] = store.stamp()
        _save_state(state)
        return {'checked': 1, 'forgotten': 0}
    if exists is None and state['yt_checked'] >= last_day:
        return {'checked': 0, 'forgotten': 0}
    state['playlist_id'] = ''
    state['yt_checked'] = None
    state['synced_ids_json'] = '[]'
    state['synced_count'] = 0
    state['last_error'] = (
        'YouTube no longer has the synced playlist' if exists is False
        else 'The synced playlist link is not kept past 30 days without a check; it stays in your YouTube library'
    )
    _save_state(state)
    return {'checked': 0, 'forgotten': 1}


def forget_youtube() -> int:
    """Clear YouTube-derived sync links/IDs when the user deletes YouTube data."""
    state = _load_state()
    had = 1 if state['playlist_id'] else 0
    state['playlist_id'] = ''
    state['yt_checked'] = None
    state['synced_ids_json'] = '[]'
    state['synced_count'] = 0
    state['pending_count'] = state['favorites_count']
    _save_state(state)
    return had


def _cli() -> None:
    parser = argparse.ArgumentParser(description='Encrypted voice-wei library sync helper')
    parser.add_argument('--init-key', action='store_true', help='Generate a new random sync key and server hash')
    parser.add_argument('--hash-key', metavar='KEY', help='Compute sync-auth.json for an existing sync key')
    parser.add_argument('--now', action='store_true', help='Run one sync pass using config.local.json')
    args = parser.parse_args()

    if args.init_key or args.hash_key:
        key = args.hash_key.strip() if args.hash_key else secrets.token_urlsafe(24)
        auth_hex, _ = derive_keys(key)
        auth_json = json.dumps({'tokenSha256': token_sha256(auth_hex)}, indent=2)
        print('1) Put this on the server at /srv/voice-wei/shared/sync-auth.json (chmod 600):')
        print(auth_json)
        print()
        print('2) Paste this Sync Key into voice-wei Settings -> Encrypted Library Sync:')
        print(key)
        print()
        print('3) Add these fields to yt-playlist-maker/config.local.json:')
        print(json.dumps({
            'sync_url': 'https://your-host/voice-wei/proxy.php',
            'sync_key': key,
        }, indent=2))
        return

    if args.now:
        cfg = config.load()
        store.setup()
        result = run(cfg, force=True)
        print(json.dumps(result, indent=2))
        return

    parser.print_help()


if __name__ == '__main__':
    _cli()
