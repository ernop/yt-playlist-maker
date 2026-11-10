from helpers import song

from yt_playlist_maker import choosing, listenbrainz


def titles(picked):
    return [s['title'] for s in picked]


def test_one_song_per_artist_in_a_line():
    pool = [song('1', 'A', 'a1'), song('2', 'A', 'a2'), song('3', 'B', 'b1')]
    picked, rest = choosing.pick(pool, None, 2, choosing.Used())
    assert titles(picked) == ['a1', 'b1']
    assert rest == []


def test_an_artist_another_line_used_waits_its_turn():
    used = choosing.Used()
    used.take(song('x', 'A', 'other line'))
    pool = [song('1', 'A', 'a1'), song('2', 'B', 'b1'), song('3', 'C', 'c1')]
    assert titles(choosing.pick(pool, None, 2, used)[0]) == ['b1', 'c1']
    used = choosing.Used()
    used.take(song('x', 'A', 'other line'))
    assert titles(choosing.pick(pool, None, 3, used)[0]) == ['b1', 'c1', 'a1']


def test_no_recording_or_song_twice_in_a_list():
    used = choosing.Used()
    used.take(song('same', 'A', 'Teardrop'))
    used.take(song('r1', 'B', 'Glory Box'))
    pool = [song('same', 'C', 'Teardrop'), song('r2', 'B', 'Glory  box!', artist_mbids=['b2']),
            song('r3', 'D', 'Roads')]
    assert titles(choosing.pick(pool, None, 3, used)[0]) == ['Roads']


def test_the_rest_is_kept_for_swap():
    pool = [song(str(n), f'artist {n}', f'song {n}') for n in range(30)]
    picked, rest = choosing.pick(pool, None, 3, choosing.Used())
    assert len(picked) == 3
    assert [s['title'] for s in rest] == [f'song {n}' for n in range(3, 3 + choosing.POOL_KEPT)]


def test_an_artist_entry_takes_its_most_listened_song_that_fits(monkeypatch):
    tops = {'a-1': [{'mbid': 'intro', 'listens': 900}, {'mbid': 'old', 'listens': 800},
                    {'mbid': 'hit', 'listens': 700}]}
    meta = {
        'intro': {'mbid': 'intro', 'name': 'Intro', 'length_ms': 40_000, 'year': 1995, 'artist': 'One',
                  'artists': [{'mbid': 'a-1', 'name': 'One', 'youtube': ''}]},
        'old': {'mbid': 'old', 'name': 'Old', 'length_ms': 200_000, 'year': 1979, 'artist': 'One',
                'artists': [{'mbid': 'a-1', 'name': 'One', 'youtube': ''}]},
        'hit': {'mbid': 'hit', 'name': 'Hit', 'length_ms': 200_000, 'year': 1994, 'artist': 'One',
                'artists': [{'mbid': 'a-1', 'name': 'One', 'youtube': 'https://www.youtube.com/@one'}]},
    }
    monkeypatch.setattr(listenbrainz, 'top_recordings', lambda mbid: tops.get(mbid, []))
    monkeypatch.setattr(listenbrainz, 'recording_metadata', lambda mbids: {m: meta[m] for m in mbids})
    picked, _ = choosing.pick([{'artist_mbid': 'a-1', 'name': 'One', 'why': 'like Two'}], [1990, 1999], 1,
                              choosing.Used())
    assert titles(picked) == ['Hit']
    assert picked[0]['why'] == 'like Two'
    assert picked[0]['artist_youtube'] == ['https://www.youtube.com/@one']


def test_an_artist_line_pools_similar_artists_without_the_seed(monkeypatch):
    monkeypatch.setattr(listenbrainz, 'similar_artists', lambda mbid: [
        {'mbid': 'seed', 'name': 'Seed'}, {'mbid': 's1', 'name': 'One'}, {'mbid': 's2', 'name': 'Two'}])
    reading = {'kind': 'artist', 'artists': [{'mbid': 'seed', 'name': 'Portishead'}], 'tags': [], 'years': None}
    assert choosing.pool(reading) == [{'artist_mbid': 's1', 'name': 'One', 'why': 'like Portishead'},
                                      {'artist_mbid': 's2', 'name': 'Two', 'why': 'like Portishead'}]


def test_a_style_line_pools_tagged_and_example_songs_by_listens(monkeypatch):
    def meta(mbid, year, length=200_000):
        return {'mbid': mbid, 'name': f'title {mbid}', 'length_ms': length, 'year': year, 'artist': f'artist {mbid}',
                'artists': [{'mbid': f'a-{mbid}', 'name': f'artist {mbid}', 'youtube': ''}]}

    all_meta = {'t1': meta('t1', 1965), 't2': meta('t2', 1985), 't3': meta('t3', 1966, 30_000),
                'e1': meta('e1', 1967)}
    monkeypatch.setattr(listenbrainz, 'tag_recordings', lambda tag: ['t1', 't2', 't3'])
    monkeypatch.setattr(listenbrainz, 'recording_listens', lambda mbids: {'t1': 50, 't2': 900, 't3': 999})
    monkeypatch.setattr(listenbrainz, 'top_recordings', lambda mbid: [{'mbid': 'e1', 'listens': 70}])
    monkeypatch.setattr(listenbrainz, 'recording_metadata', lambda mbids: {m: all_meta[m] for m in mbids})
    reading = {'kind': 'style', 'artists': [{'mbid': 'mg', 'name': 'Marvin Gaye'}], 'tags': ['motown'],
               'years': [1960, 1969]}
    pooled = choosing.pool(reading)
    assert [(s['mbid'], s['why']) for s in pooled] == [('e1', 'by Marvin Gaye, named for this line'),
                                                       ('t1', 'tagged motown')]
