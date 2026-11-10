import pytest

from yt_playlist_maker import listenbrainz, model, musicbrainz, reading


def entry(line, kind='style', artists=(), tags=(), years=None):
    return {'line': line, 'kind': kind, 'artists': list(artists), 'tags': list(tags), 'years': years}


def mb_artist(mbid, name, *, sort_name=None, comment='', score=100, aliases=()):
    return {'mbid': mbid, 'name': name, 'sort_name': sort_name or name, 'comment': comment, 'score': score,
            'aliases': list(aliases)}


def test_parse_tidies_the_answer():
    answer = {'lines': [
        entry(2, 'artist', ['The Beatles', 'Wings'], years={'from': 1966, 'to': 1970}),
        entry(1, 'style', ['A', 'B', 'C', 'D', 'E', 'F', 'A'], ['Trip  Hop', 'trip hop', 'Downtempo', 'bristol', 'x']),
    ]}
    first, second = reading.parse(answer, 2)
    assert first == {'kind': 'style', 'artists': ['A', 'B', 'C', 'D', 'E'],
                     'tags': ['trip hop', 'downtempo', 'bristol'], 'years': None}
    assert second == {'kind': 'artist', 'artists': ['The Beatles'], 'tags': [], 'years': [1966, 1970]}


@pytest.mark.parametrize('years', [{'from': 1990, 'to': 1980}, {'from': 999, 'to': 1000}, {'from': 1990},
                                   {'from': 2020, 'to': 3000}, 'the 80s'])
def test_years_that_are_no_period_are_dropped(years):
    assert reading.parse({'lines': [entry(1, years=years)]}, 1)[0]['years'] is None


@pytest.mark.parametrize('answer, words', [
    ({'lines': [entry(1)]}, '1 lines of 2'),
    ({'lines': [entry(1), entry(1)]}, 'one entry for each line'),
    ({'lines': 'nothing'}, '0 lines of 2'),
])
def test_a_wrong_count_is_a_bad_answer(answer, words):
    with pytest.raises(model.BadAnswer, match=words):
        reading.parse(answer, 2)


def test_an_artist_line_needs_its_artist():
    with pytest.raises(model.BadAnswer, match='without an artist'):
        reading.parse({'lines': [entry(1, 'artist', [' '])]}, 1)


def test_an_unknown_kind_is_a_bad_answer():
    with pytest.raises(model.BadAnswer, match='kind'):
        reading.parse({'lines': [entry(1, 'mood')]}, 1)


def test_fold():
    assert reading.fold('  Bj\u00f6rk -- Army of Me!') == 'bjork army of me'
    assert reading.fold('\u0421\u043f\u043b\u0438\u043d') == '\u0441\u043f\u043b\u0438\u043d'


def test_of_artists_with_the_name_the_most_listened_is_taken(monkeypatch):
    monkeypatch.setattr(musicbrainz, 'search_artists', lambda name: [
        mb_artist('g1', 'Genesis', comment='US band'),
        mb_artist('g2', 'Genesis', comment='English rock band'),
        mb_artist('other', 'Genesis P-Orridge', score=95),
    ])
    monkeypatch.setattr(listenbrainz, 'artist_listens', lambda mbids: {'g1': 10, 'g2': 9000})
    found = reading.find_artist('genesis')
    assert (found['mbid'], found['comment'], found['note']) == ('g2', 'English rock band', '')
    assert found['others'] == [{'mbid': 'g1', 'name': 'Genesis', 'comment': 'US band'}]


def test_an_alias_or_sort_name_counts_as_the_name(monkeypatch):
    monkeypatch.setattr(musicbrainz, 'search_artists', lambda name: [
        mb_artist('p', 'Prince'), mb_artist('t', 'The The', sort_name='The, The', aliases=['TAFKAP'])])
    assert reading.find_artist('Tafkap')['mbid'] == 't'


def test_else_the_closest_name_says_so(monkeypatch):
    monkeypatch.setattr(musicbrainz, 'search_artists', lambda name: [mb_artist('n', 'Nina Simone', score=92)])
    found = reading.find_artist('Nina Simon')
    assert (found['mbid'], found['note']) == ('n', 'the closest name in MusicBrainz')


def test_else_none(monkeypatch):
    monkeypatch.setattr(musicbrainz, 'search_artists', lambda name: [mb_artist('x', 'Somebody', score=60)])
    found = reading.find_artist('Nobody At All')
    assert found['mbid'] is None and found['note'] == 'MusicBrainz has no artist of that name'


def test_artist_lines_skip_the_model(monkeypatch):
    asked = []

    def ask(model_name, instructions, text, schema):
        asked.append(text)
        return {'lines': [entry(1, 'style', ['Marvin Gaye'], ['motown'], {'from': 1960, 'to': 1969})]}

    monkeypatch.setattr(model, 'ask', ask)
    monkeypatch.setattr(musicbrainz, 'search_artists', lambda name: [mb_artist(f'id-{name}', name)])
    readings = reading.read(['artist: Portishead', '60s Motown'], 'm')
    assert asked == ['1. 60s Motown']
    assert readings[0]['by'] == 'you' and readings[0]['artists'][0]['mbid'] == 'id-Portishead'
    assert readings[1]['by'] == 'm' and readings[1]['years'] == [1960, 1969]


def test_no_model_call_when_every_line_is_an_artist(monkeypatch):
    monkeypatch.setattr(model, 'ask', lambda *a: pytest.fail('the model was asked'))
    monkeypatch.setattr(musicbrainz, 'search_artists', lambda name: [mb_artist('b', name)])
    assert reading.read(['Artist:  Bj\u00f6rk '], 'm')[0]['artists'][0]['name'] == 'Bj\u00f6rk'
