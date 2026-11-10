from datetime import timedelta

import pytest
from helpers import song, video

from yt_playlist_maker import net, store, videos, youtube

TEARDROP = song('t', 'Massive Attack', 'Teardrop', length_ms=330_000)


def test_core_title_drops_what_brackets_add():
    assert videos.core_title('Teardrop (Remastered 2006) [feat. Liz Fraser]') == 'teardrop'
    assert videos.core_title('(Interlude)') == 'interlude'


@pytest.mark.parametrize('clip, ok', [
    (video('Massive Attack - Teardrop', duration_s=331), True),
    (video('Massive Attack - Teardrop', duration_s=331, playable=False), False),
    (video('Massive Attack - Angel', duration_s=331), False),
    (video('Massive Attack - Teardrop (Live at Glastonbury)', duration_s=331), False),
    (video('Teardrop - karaoke version', duration_s=331), False),
    (video('Massive Attack - Teardrop', duration_s=350), False),
    (video('Massive Attack - Teardrop', duration_s=300), False),
])
def test_when_a_video_is_the_song(clip, ok):
    assert (videos.match(TEARDROP, clip, set()) is not None) == ok


def test_a_word_in_the_songs_own_name_is_allowed():
    live = song('l', 'Wings', 'Live and Let Die', length_ms=192_000)
    assert videos.match(live, video('Wings - Live and Let Die', duration_s=190), set()) is not None


def test_the_artists_channel_may_be_longer():
    clip = video('Teardrop (Official Video)', channel_id='UCown', duration_s=330 + 85)
    assert videos.match(TEARDROP, clip, {'UCown'}) == (0, 85)
    assert videos.match(TEARDROP, clip, set()) is None


def test_open_data_needs_no_title():
    assert videos.match(TEARDROP, video('MA 1998 #4', duration_s=330), set(), title_needed=False) is not None


def test_unknown_length_matches_by_title():
    assert videos.match(song('x', 'A', 'Song', length_ms=None), video('A - Song', duration_s=999), set()) == (2, 0)


def test_best_prefers_own_channel_then_topic_then_nearest_length():
    details = {
        'near': video('Teardrop', duration_s=331),
        'topic': video('Teardrop', channel='Massive Attack - Topic', duration_s=340),
        'own': video('Teardrop', channel_id='UCown', duration_s=380),
    }
    assert videos.best(TEARDROP, details, list(details), {'UCown'}) == 'own'
    assert videos.best(TEARDROP, details, list(details), set()) == 'topic'
    assert videos.best(TEARDROP, {'near': details['near']}, ['near', 'missing'], set()) == 'near'


def test_without_a_key_nothing_is_looked_for():
    with pytest.raises(youtube.NoKey):
        videos.find([TEARDROP], '')


def found_row(mbid, source, days_old):
    videos.save(mbid, 'found', ['open'], f'v-{mbid}', source,
                {'title': 'T', 'channel': 'C', 'duration_s': 200})
    old = store.iso(net.now() - timedelta(days=days_old))
    with store.db() as con:
        con.execute('UPDATE videos SET yt_checked = ? WHERE recording_mbid = ?', (old, mbid))


def rows():
    return videos.known()


def test_refresh_checks_from_day_25(monkeypatch):
    found_row('fresh', 'search', 3)
    found_row('due', 'search', 26)
    found_row('removed', 'channel', 27)
    asked = []

    def check(ids, key, keep=True):
        asked.append((sorted(ids), keep))
        return {'v-due': {'title': 'New title', 'channel': 'C', 'duration_s': 201, 'playable': True}}

    monkeypatch.setattr(youtube, 'videos', check)
    assert videos.refresh('KEY') == {'checked': 2, 'gone': 1, 'deleted': 0}
    assert asked == [(['v-due', 'v-removed'], False)]
    now = rows()
    assert now['due']['yt_title'] == 'New title'
    assert store.at(now['due']['yt_checked']) > net.now() - timedelta(minutes=1)
    assert now['removed']['state'] == 'gone' and now['removed']['video_id'] is None


def test_refresh_that_fails_deletes_only_what_is_30_days_old(monkeypatch):
    found_row('day26', 'search', 26)
    found_row('day31-search', 'search', 31)
    found_row('day31-open', 'musicbrainz', 31)

    def fails(ids, key, keep=True):
        raise net.Unreachable('down')

    monkeypatch.setattr(youtube, 'videos', fails)
    assert videos.refresh('KEY')['deleted'] == 2
    now = rows()
    assert now['day26']['yt_title'] == 'T'
    assert 'day31-search' not in now
    assert now['day31-open']['video_id'] == 'v-day31-open' and now['day31-open']['yt_title'] is None
