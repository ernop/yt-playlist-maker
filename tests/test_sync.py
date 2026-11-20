import json
import subprocess

import pytest
from helpers import FakeReply

from yt_playlist_maker import google_auth, net, sync, youtube
from yt_playlist_maker.config import Config

# Exact cross-language vector shared with voice-wei/tests/test-player-sync.js.
VECTOR_SYNC_KEY = 'voice-wei-cross-language-test-key-0123456789'


def sync_cfg(**overrides) -> Config:
    base = {
        'port': 8020,
        'listenbrainz_token': 'LBTOKEN',
        'youtube_api_key': 'KEY',
        'google_client_id': 'client',
        'google_client_secret': 'secret',
        'language_model': 'test-model',
        'sync_url': 'https://sync.example/voice-wei/proxy.php',
        'sync_key': VECTOR_SYNC_KEY,
        'sync_playlist_id': '',
        'sync_title': 'Favorite Songs',
        'sync_visibility': 'unlisted',
        'sync_minutes': 15,
    }
    base.update(overrides)
    return Config(**base)


def test_hkdf_and_aes_gcm_roundtrip_and_cross_language_vector():
    auth_hex, enc_key = sync.derive_keys(VECTOR_SYNC_KEY)
    assert len(auth_hex) == 64 and len(enc_key) == 32
    token_hash = sync.token_sha256(auth_hex)
    assert len(token_hash) == 64

    # Cross-check against Node.js crypto.hkdfSync if node is available on this machine.
    script = (
        "const c = require('crypto');"
        f"const k = Buffer.from({VECTOR_SYNC_KEY!r}, 'utf8');"
        "const s = Buffer.from('voice-wei-sync-v1', 'utf8');"
        "const auth = Buffer.from(c.hkdfSync('sha256', k, s, Buffer.from('auth'), 32)).toString('hex');"
        "const enc = Buffer.from(c.hkdfSync('sha256', k, s, Buffer.from('encrypt'), 32)).toString('hex');"
        "const hash = c.createHash('sha256').update(auth, 'utf8').digest('hex');"
        "console.log(JSON.stringify({auth, enc, hash}));"
    )
    out = subprocess.check_output(['node', '-e', script], text=True)
    node_vec = json.loads(out)
    assert auth_hex == node_vec['auth']
    assert enc_key.hex() == node_vec['enc']
    assert token_hash == node_vec['hash']

    sample = sync.empty_snapshot()
    sample['favorites']['abc12345678'] = {
        'videoId': 'abc12345678',
        'name': 'Roads',
        'artist': 'Portishead',
        'favoritedAt': 1700000000000,
        'updatedAt': 1700000000000,
    }
    blob = sync.encrypt_snapshot(sample, enc_key)
    assert blob[:4] == b'VWS1'
    decoded = sync.decrypt_snapshot(blob, enc_key)
    assert decoded['favorites']['abc12345678']['name'] == 'Roads'

    # True bidirectional ciphertext interop with browser WebCrypto (subtle.encrypt / subtle.decrypt).
    interop_js = (
        "(async () => {"
        "  const subtle = globalThis.crypto.subtle;"
        "  const enc = new TextEncoder();"
        "  const dec = new TextDecoder();"
        f"  const base = await subtle.importKey('raw', enc.encode({VECTOR_SYNC_KEY!r}), 'HKDF', false, ['deriveKey']);"
        "  const aesKey = await subtle.deriveKey("
        "    { name: 'HKDF', hash: 'SHA-256', salt: enc.encode('voice-wei-sync-v1'), info: enc.encode('encrypt') },"
        "    base, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);"
        f"  const fromPy = Buffer.from({blob.hex()!r}, 'hex');"
        "  const plainBuf = await subtle.decrypt("
        "    { name: 'AES-GCM', iv: fromPy.subarray(4, 16), additionalData: enc.encode('VWS1') },"
        "    aesKey, fromPy.subarray(16));"
        "  const parsed = JSON.parse(dec.decode(plainBuf));"
        "  const jsSnap = { ...parsed, title: 'From Browser WebCrypto' };"
        "  const iv = globalThis.crypto.getRandomValues(new Uint8Array(12));"
        "  const ct = new Uint8Array(await subtle.encrypt("
        "    { name: 'AES-GCM', iv, additionalData: enc.encode('VWS1') },"
        "    aesKey, enc.encode(JSON.stringify(jsSnap))));"
        "  const jsBlob = Buffer.concat([Buffer.from('VWS1'), Buffer.from(iv), Buffer.from(ct)]);"
        "  console.log(JSON.stringify({ pyName: parsed.favorites.abc12345678.name, jsBlobHex: jsBlob.toString('hex') }));"
        "})();"
    )
    interop_out = json.loads(subprocess.check_output(['node', '-e', interop_js], text=True))
    assert interop_out['pyName'] == 'Roads'
    from_js = sync.decrypt_snapshot(bytes.fromhex(interop_out['jsBlobHex']), enc_key)
    assert from_js['title'] == 'From Browser WebCrypto'
    assert from_js['favorites']['abc12345678']['artist'] == 'Portishead'

    _, wrong_key = sync.derive_keys('different-wrong-sync-key-9876543210')
    with pytest.raises(sync.SyncError, match='Could not decrypt'):
        sync.decrypt_snapshot(blob, wrong_key)

    tampered = bytearray(blob)
    tampered[-1] ^= 0xFF
    with pytest.raises(sync.SyncError, match='Could not decrypt'):
        sync.decrypt_snapshot(bytes(tampered), enc_key)


def test_crdt_merge_honors_tombstones_and_re_stars():
    local = sync.empty_snapshot()
    local['favorites']['vidA0000001'] = {
        'videoId': 'vidA0000001', 'name': 'Song A', 'artist': 'Artist A',
        'favoritedAt': 1000, 'updatedAt': 1000,
    }
    local['favorites']['vidB0000002'] = {
        'videoId': 'vidB0000002', 'name': 'Song B', 'artist': 'Artist B',
        'favoritedAt': 2000, 'updatedAt': 2000,
    }

    remote = sync.empty_snapshot()
    # Unstarred vidA at t=3000 -> wins over t=1000.
    remote['removedFavorites']['vidA0000001'] = 3000
    # Unstarred vidB at t=1500 -> older than local star at t=2000, so vidB stays starred.
    remote['removedFavorites']['vidB0000002'] = 1500
    remote['favorites']['vidC0000003'] = {
        'videoId': 'vidC0000003', 'name': 'Song C', 'artist': 'Artist C',
        'favoritedAt': 2500, 'updatedAt': 2500,
    }

    merged = sync.merge_snapshots(local, remote, apply_tombstones=True)
    assert 'vidA0000001' not in merged['favorites']
    assert merged['removedFavorites']['vidA0000001'] == 3000
    assert 'vidB0000002' in merged['favorites']
    assert 'vidB0000002' not in merged['removedFavorites']
    assert 'vidC0000003' in merged['favorites']


def test_two_way_safe_sync_pushes_phone_favorites_and_pulls_youtube_additions(session, monkeypatch, tmp_path):
    cfg = sync_cfg()
    auth_hex, enc_key = sync.derive_keys(cfg.sync_key)
    (tmp_path / 'google_token.json').write_text(json.dumps({
        'refresh_token': 'r', 'access_token': 'tok', 'expires_at': 9999999999,
    }))

    phone_snap = sync.empty_snapshot()
    phone_snap['favorites']['phoneVid001'] = {
        'videoId': 'phoneVid001', 'name': 'Phone Song', 'artist': 'Phone Artist',
        'favoritedAt': 1700000000000, 'updatedAt': 1700000000000,
    }
    phone_snap['favorites']['goneVid0002'] = {
        'videoId': 'goneVid0002', 'name': 'Deleted Song', 'artist': 'Ghost',
        'favoritedAt': 1700000001000, 'updatedAt': 1700000001000,
    }
    phone_snap['removedFavorites']['oldUnstar03'] = 1700005000000
    phone_snap['songs'] = list(phone_snap['favorites'].values())
    initial_blob = sync.encrypt_snapshot(phone_snap, enc_key)

    posted_blobs: list[bytes] = []

    def fake_request(method, url, headers=None, json=None, data=None, timeout=None, params=None):
        session.calls.append({'method': method, 'url': url, 'headers': headers, 'data': data, 'params': params})
        if 'sync.example' in url:
            assert headers['X-Voice-Wei-Sync-Token'] == auth_hex
            if method == 'GET':
                return FakeReply(200, headers={'ETag': '"etag1"'}, content=initial_blob)
            if method == 'POST':
                posted_blobs.append(data)
                return FakeReply(200, data={'ok': True, 'etag': 'etag2'}, headers={'ETag': '"etag2"'})
        raise AssertionError(f'unexpected {method} {url}')

    monkeypatch.setattr(session, 'request', fake_request)

    added_to_yt: list[tuple[str, str, int]] = []
    monkeypatch.setattr(youtube, 'playlist_items', lambda pid, key='', token='', keep=False: [
        # Added on PC YouTube AFTER everything -> should be pulled into voice-wei!
        {'video_id': 'pcAdded0004', 'title': 'PC Song', 'channel': 'PC Artist', 'added_at_ms': 1700010000000},
        # Added earlier than voice-wei's unstar tombstone -> should NOT resurrect!
        {'video_id': 'oldUnstar03', 'title': 'Old Unstarred', 'channel': 'Old', 'added_at_ms': 1700001000000},
    ])
    monkeypatch.setattr(youtube, 'videos', lambda ids, key='', keep=False, token='': {
        'phoneVid001': {'title': 'Phone Song', 'channel': 'Phone Artist', 'channel_id': 'UC1',
                        'duration_s': 200, 'playable': True},
        # goneVid0002 omitted -> marked gone (unavailable) without spending 50 units!
    })
    monkeypatch.setattr(youtube, 'add_to_playlist', lambda token, pid, vid, pos: added_to_yt.append((pid, vid, pos)))

    cfg = sync_cfg(sync_playlist_id='PLexisting0001')
    result = sync.run(cfg, force=True)

    assert added_to_yt == [('PLexisting0001', 'phoneVid001', 2)]
    assert result['favorites_count'] == 3  # phoneVid001, goneVid0002, pcAdded0004 (never auto-unstarred!)
    assert result['synced_count'] == 2     # phoneVid001 + pcAdded0004
    assert result['unavailable_count'] == 1
    assert result['pending_count'] == 0
    assert result['pulled_from_youtube'] == 1

    # Confirm the updated encrypted blob pushed back to the sync host contains pcAdded0004 and NOT oldUnstar03.
    assert len(posted_blobs) == 1
    pushed = sync.decrypt_snapshot(posted_blobs[0], enc_key)
    assert 'pcAdded0004' in pushed['favorites']
    assert 'phoneVid001' in pushed['favorites']
    assert 'goneVid0002' in pushed['favorites']
    assert 'oldUnstar03' not in pushed['favorites']


def test_sync_pauses_cleanly_on_over_budget_and_keeps_progress(session, monkeypatch, tmp_path):
    cfg = sync_cfg(sync_playlist_id='PLbudget00001')
    _, enc_key = sync.derive_keys(cfg.sync_key)
    (tmp_path / 'google_token.json').write_text(json.dumps({
        'refresh_token': 'r', 'access_token': 'tok', 'expires_at': 9999999999,
    }))

    snap = sync.empty_snapshot()
    for i in (1, 2):
        vid = f'budgetVid0{i}'
        snap['favorites'][vid] = {'videoId': vid, 'name': f'Song {i}', 'artist': 'A', 'favoritedAt': i * 1000, 'updatedAt': i * 1000}
    snap['songs'] = list(snap['favorites'].values())
    session.route('sync.example', FakeReply(200, headers={'ETag': '"etag1"'}, content=sync.encrypt_snapshot(snap, enc_key)))

    monkeypatch.setattr(youtube, 'playlist_items', lambda pid, key='', token='', keep=False: [])
    monkeypatch.setattr(youtube, 'videos', lambda ids, key='', keep=False, token='': {
        vid: {'title': vid, 'channel': 'A', 'channel_id': 'UC1', 'duration_s': 180, 'playable': True} for vid in ids
    })
    calls = {'n': 0}

    def add_limited(token, pid, vid, pos):
        calls['n'] += 1
        if calls['n'] > 1:
            raise net.OverBudget("YouTube's daily units", net.next_pacific_midnight())

    monkeypatch.setattr(youtube, 'add_to_playlist', add_limited)
    result = sync.run(cfg, force=True)
    assert result['synced_count'] == 1
    assert result['pending_count'] == 1
    assert result['wait_until']
    assert 'daily units' in result['wait_reason']
