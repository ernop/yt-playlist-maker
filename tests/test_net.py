from datetime import UTC, datetime

import pytest
from helpers import FakeReply

from yt_playlist_maker import net, store


def test_requests_name_the_app_and_its_contact(session):
    session.route('musicbrainz.org', FakeReply(data={'ok': 1}))
    net.call('musicbrainz', 'GET', 'https://musicbrainz.org/ws/2/artist', params={'query': 'x'})
    agent = session.calls[0]['headers']['User-Agent']
    assert agent == f'yt-playlist-maker/{net.VERSION} ( https://github.com/ernop/yt-playlist-maker )'


def test_google_gets_only_the_app_name(session):
    session.route('googleapis.com', FakeReply(data={'items': []}))
    net.call('youtube', 'GET', 'https://www.googleapis.com/youtube/v3/videos', params={'key': 'K'}, units=1)
    assert session.calls[0]['headers']['User-Agent'] == f'yt-playlist-maker/{net.VERSION}'


def test_a_kept_answer_is_not_asked_again(session):
    session.route('listenbrainz', FakeReply(data=[1, 2]))
    first = net.call('listenbrainz', 'GET', 'https://api.listenbrainz.org/1/x', params={'a': 1})
    second = net.call('listenbrainz', 'GET', 'https://api.listenbrainz.org/1/x', params={'a': 1})
    assert first == second == [1, 2]
    assert len(session.calls) == 1


def test_a_new_key_finds_the_kept_answer(session):
    session.route('googleapis.com', FakeReply(data={'items': ['v']}))
    for key in ('OLD', 'NEW'):
        net.call('youtube', 'GET', 'https://www.googleapis.com/youtube/v3/videos', params={'id': 'v', 'key': key},
                 units=1)
    assert len(session.calls) == 1


def test_keep_false_always_asks(session):
    session.route('googleapis.com', FakeReply(data={}))
    for _ in range(2):
        net.call('youtube', 'GET', 'https://www.googleapis.com/youtube/v3/playlists', params={'key': 'K'},
                 units=1, keep=False)
    assert len(session.calls) == 2


def test_units_are_counted_before_the_request(session):
    session.route('googleapis.com', FakeReply(403, {'error': {'message': 'quotaExceeded'}}))
    with pytest.raises(net.ServiceError, match='quotaExceeded'):
        net.call('youtube', 'POST', 'https://www.googleapis.com/youtube/v3/playlists', units=50, keep=False)
    assert net.youtube_left()['units'] == net.YOUTUBE_UNITS_PER_DAY - 50


def test_past_the_budget_nothing_is_sent(session):
    store.spend('youtube-units', net.pacific_day(), net.YOUTUBE_UNITS_PER_DAY - 10, net.YOUTUBE_UNITS_PER_DAY)
    with pytest.raises(net.OverBudget) as raised:
        net.call('youtube', 'POST', 'https://www.googleapis.com/youtube/v3/playlistItems', units=50, keep=False)
    assert session.calls == []
    assert raised.value.until == net.next_pacific_midnight()


def test_searches_have_their_own_budget(session):
    session.route('search', FakeReply(data={'items': []}))
    for n in range(net.YOUTUBE_SEARCHES_PER_DAY):
        net.call('youtube', 'GET', 'https://www.googleapis.com/youtube/v3/search', params={'q': str(n)},
                 search=True)
    with pytest.raises(net.OverBudget, match='search'):
        net.call('youtube', 'GET', 'https://www.googleapis.com/youtube/v3/search', params={'q': 'one more'},
                 search=True)
    assert len(session.calls) == net.YOUTUBE_SEARCHES_PER_DAY
    assert net.youtube_left()['units'] == net.YOUTUBE_UNITS_PER_DAY


def test_busy_waits_what_the_service_says_then_gives_up(session):
    busy = FakeReply(503, {'error': 'busy'}, {'Retry-After': '7'})
    session.route('musicbrainz.org', busy)
    with pytest.raises(net.ServiceError, match='503'):
        net.call('musicbrainz', 'GET', 'https://musicbrainz.org/ws/2/artist')
    assert len(session.calls) == net.BUSY_TRIES + 1
    assert session.slept == [7.0] * net.BUSY_TRIES


def test_busy_then_answered(session):
    session.route('listenbrainz', FakeReply(429, {}, {'X-RateLimit-Reset-In': '500'}), FakeReply(data={'a': 1}))
    assert net.call('listenbrainz', 'GET', 'https://api.listenbrainz.org/1/y') == {'a': 1}
    assert session.slept == [120.0]


def test_an_answer_that_is_not_json_is_an_error(session):
    reply = FakeReply(data=None)
    reply.json = lambda: (_ for _ in ()).throw(ValueError('no'))
    session.route('wikidata', reply)
    with pytest.raises(net.ServiceError, match='not JSON'):
        net.call('wikidata', 'GET', 'https://query.wikidata.org/sparql')


@pytest.mark.parametrize('at, midnight', [
    ('2026-11-09T22:00:00+00:00', '2026-11-10T08:00:00+00:00'),  # 14:00 PST
    ('2026-11-10T07:59:00+00:00', '2026-11-10T08:00:00+00:00'),  # 23:59 PST
    ('2026-03-08T07:30:00+00:00', '2026-03-08T08:00:00+00:00'),  # 23:30 PST, before the change to PDT
    ('2026-03-08T12:00:00+00:00', '2026-03-09T07:00:00+00:00'),  # 05:00 PDT
])
def test_youtubes_day_ends_at_pacific_midnight(at, midnight):
    assert net.next_pacific_midnight(datetime.fromisoformat(at)) == datetime.fromisoformat(midnight).astimezone(UTC)
