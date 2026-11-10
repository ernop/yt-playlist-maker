import json

import pytest
from helpers import FakeReply

from yt_playlist_maker import google_auth, store, videos, web

HOST = {'Host': web.HOSTNAME}


@pytest.fixture
def client(cfg):
    return web.create(cfg).test_client()


def test_another_host_is_refused(client):
    assert client.get('/api/state', headers={'Host': 'evil.example'}).status_code == 403
    assert client.get('/api/state', headers=HOST).status_code == 200
    assert client.get('/api/state', headers={'Host': '127.0.0.1:8020'}).status_code == 200


def test_a_change_must_be_json(client):
    reply = client.post('/api/lists', data={'text': 'x'}, headers=HOST)
    assert reply.status_code == 415


def test_a_list_is_made_and_shown(client):
    reply = client.post('/api/lists', json={'text': 'Portishead\n80s synth pop', 'songs': 4}, headers=HOST)
    assert reply.status_code == 201
    shown = client.get(f'/api/lists/{reply.json["id"]}', headers=HOST).json
    assert [line['text'] for line in shown['lines']] == ['Portishead', '80s synth pop']
    assert (shown['step'], shown['songs_per_line']) == ('reading', 4)


def test_a_wrong_list_says_why(client):
    reply = client.post('/api/lists', json={'text': 'a', 'songs': 9}, headers=HOST)
    assert reply.status_code == 400 and 'Songs per line' in reply.json['error']
    assert client.get('/api/lists/999', headers=HOST).status_code == 404


def test_the_playlist_needs_a_sign_in(client):
    list_id = client.post('/api/lists', json={'text': 'a', 'songs': 2}, headers=HOST).json['id']
    reply = client.post(f'/api/lists/{list_id}/playlist', json={'visibility': 'unlisted'}, headers=HOST)
    assert reply.status_code == 400 and 'Sign in' in reply.json['error']


def test_sign_in_asks_google_for_the_youtube_scope_with_pkce(client):
    reply = client.get('/google/signin?back=/?list=3', headers=HOST)
    assert reply.status_code == 302
    url = reply.headers['Location']
    assert url.startswith(google_auth.AUTH)
    for part in ('scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fyoutube', 'code_challenge_method=S256',
                 'redirect_uri=http%3A%2F%2F127.0.0.1%3A8020%2Fgoogle%2Fcallback', 'access_type=offline'):
        assert part in url


def test_delete_youtube_data_works_when_google_cannot_be_reached(client, session, tmp_path):
    (tmp_path / 'google_token.json').write_text(json.dumps({'refresh_token': 'r', 'access_token': 'a',
                                                            'expires_at': 0}))
    videos.save('found-by-search', 'found', ['open', 'channel', 'search'], 'v1', 'search',
                {'title': 't', 'channel': 'c', 'duration_s': 1})
    videos.save('open-data', 'found', ['open'], 'v2', 'musicbrainz', {'title': 't', 'channel': 'c', 'duration_s': 1})
    session.route('oauth2.googleapis.com/revoke', FakeReply(500, {'error': 'backend'}))
    reply = client.post('/api/google/signout', json={'forget': True}, headers=HOST)
    assert reply.status_code == 502
    assert not google_auth.signed_in()
    left = videos.known()
    assert 'found-by-search' not in left
    assert left['open-data']['video_id'] == 'v2' and left['open-data']['yt_title'] is None
    with store.db() as con:
        assert con.execute("SELECT count(*) FROM answers WHERE service = 'youtube'").fetchone()[0] == 0
