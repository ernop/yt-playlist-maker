import pytest
from helpers import FakeReply

from yt_playlist_maker import lists, listenbrainz, store, worker


def test_the_api_gets_his_token_and_labs_does_not(session):
    session.route('labs.api.listenbrainz.org', FakeReply(data=[
        {'reference_mbid': 'p', 'artist_mbid': 'p', 'name': 'Portishead', 'score': 9},
        {'reference_mbid': 'p', 'artist_mbid': 'm', 'name': 'Massive Attack', 'score': 7781},
        {'reference_mbid': 'p', 'artist_mbid': 't', 'name': 'Tricky', 'score': 9000},
    ]))
    session.route('top-recordings-for-artist', FakeReply(data=[
        {'recording_mbid': 'r', 'recording_name': 'Teardrop', 'artist_name': 'Massive Attack', 'artist_mbids': ['m'],
         'length': 330_000, 'total_listen_count': 5},
    ]))
    assert [a['name'] for a in listenbrainz.similar_artists('p')] == ['Tricky', 'Massive Attack']
    assert listenbrainz.top_recordings('m')[0]['name'] == 'Teardrop'
    labs, api = session.calls
    assert 'Authorization' not in labs['headers']
    assert api['headers']['Authorization'] == 'Token LBTOKEN'


def test_an_artist_unknown_to_listenbrainz_has_no_songs(session):
    session.route('top-recordings-for-artist', FakeReply(404, {'error': 'not found'}))
    assert listenbrainz.top_recordings('nobody') == []


def test_without_a_token_the_list_says_what_is_missing(monkeypatch, cfg):
    monkeypatch.setattr(listenbrainz, '_token', '')
    list_id = lists.create('80s synth pop', 2)
    lists.set_step(list_id, 'choosing')
    with pytest.raises(listenbrainz.NoToken):
        listenbrainz.tag_recordings('synth-pop')
    reading = '{"kind": "style", "artists": [], "tags": ["synth-pop"], "years": [1980, 1989]}'
    with store.db() as con:
        con.execute('UPDATE lines SET reading = ?', (reading,))
    worker.advance(cfg, list_id)
    assert 'listenbrainz_token' in lists.get(list_id)['error']
