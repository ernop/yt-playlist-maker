import json
from datetime import timedelta

import pytest
from helpers import song

from yt_playlist_maker import choosing, google_auth, lists, model, net, playlists, store, videos, worker, youtube


def test_split_counts_a_repeated_line_once():
    assert lists.split(' Nina   Simone \n\n80s synth pop\nNina Simone\n') == ['Nina Simone', '80s synth pop']


@pytest.mark.parametrize('text, words', [
    ('\n  \n', 'at least one line'),
    ('\n'.join(f'line {n}' for n in range(lists.MAX_LINES + 1)), f'At most {lists.MAX_LINES} lines'),
    ('ok\n' + 'x' * (lists.MAX_LINE_CHARS + 1), 'Line 2 is longer'),
])
def test_split_refuses(text, words):
    with pytest.raises(ValueError, match=words):
        lists.split(text)


def test_songs_per_line_is_2_to_5():
    with pytest.raises(ValueError, match='Songs per line'):
        lists.create('a', 6)


def made_list(*songs_by_line, songs_per_line=2):
    list_id = lists.create('\n'.join(f'line {n}' for n in range(len(songs_by_line))), songs_per_line)
    whole = lists.get(list_id)
    with store.db() as con:
        for line, songs in zip(whole['lines'], songs_by_line):
            for position, one in enumerate(songs):
                lists._insert(con, line['id'], position, one)
            con.execute("UPDATE lines SET chosen = 1, reading = ? WHERE id = ?",
                        ('{"kind": "style", "artists": [], "tags": ["x"], "years": null}', line['id']))
    lists.set_step(list_id, 'ready')
    return list_id


def test_swap_takes_the_next_candidate_and_looks_for_its_video():
    list_id = made_list([song('1', 'A', 'a1'), song('2', 'B', 'b1')])
    line = lists.get(list_id)['lines'][0]
    pool = [song('3', 'A', 'a2'), song('4', 'C', 'c1'), song('5', 'D', 'd1')]
    with store.db() as con:
        con.execute('UPDATE lines SET pool = ? WHERE id = ?', (json.dumps(pool), line['id']))
    lists.swap(line['picks'][0]['id'])
    after = lists.get(list_id)
    assert [p['title'] for p in after['lines'][0]['picks']] == ['c1', 'b1']
    assert after['lines'][0]['pool_size'] == 1
    assert after['step'] == 'videos'


def test_swap_with_no_candidates_says_so():
    list_id = made_list([song('1', 'A', 'a1')])
    with pytest.raises(lists.Failed, match='no more candidates'):
        lists.swap(lists.get(list_id)['lines'][0]['picks'][0]['id'])


def test_choose_step_skips_what_earlier_lines_took(monkeypatch):
    list_id = lists.create('first\nsecond', 2)
    with store.db() as con:
        con.execute("UPDATE lines SET reading = ?",
                    ('{"kind": "style", "artists": [], "tags": ["x"], "years": null}',))
    lists.set_step(list_id, 'choosing')
    monkeypatch.setattr(choosing, 'pool', lambda reading: [song('1', 'A', 'a1'), song('2', 'B', 'b1'),
                                                           song('3', 'C', 'c1')])
    lists.choose_step(list_id)
    whole = lists.get(list_id)
    assert [[p['title'] for p in line['picks']] for line in whole['lines']] == [['a1', 'b1'], ['c1']]
    assert whole['step'] == 'videos'


def failing_step(monkeypatch, exc):
    list_id = lists.create('a', 2)

    def fail(*args):
        raise exc

    monkeypatch.setattr(lists, 'read_step', fail)
    return list_id


def test_a_busy_gpu_waits_five_minutes(monkeypatch, cfg):
    list_id = failing_step(monkeypatch, model.Unavailable('the GPU is busy'))
    worker.advance(cfg, list_id)
    whole = lists.get(list_id)
    assert whole['error'] == '' and 'GPU is busy' in whole['wait_reason']
    assert store.at(whole['wait_until']) > net.now() + timedelta(minutes=4)
    assert worker._due('lists', "step IN ('reading', 'choosing', 'videos')") is None


def test_past_the_budget_waits_until_it_is_new(monkeypatch, cfg):
    until = net.next_pacific_midnight()
    list_id = failing_step(monkeypatch, net.OverBudget("YouTube's search budget", until))
    worker.advance(cfg, list_id)
    assert store.at(lists.get(list_id)['wait_until']) == until


def test_a_failing_service_is_tried_three_times(monkeypatch, cfg):
    list_id = failing_step(monkeypatch, net.ServiceError('musicbrainz', 503, 'busy'))
    for _ in range(worker.MAX_TRIES - 1):
        worker.advance(cfg, list_id)
        assert lists.get(list_id)['error'] == ''
    worker.advance(cfg, list_id)
    assert 'HTTP 503' in lists.get(list_id)['error']


def test_a_refusal_stops_at_once(monkeypatch, cfg):
    list_id = failing_step(monkeypatch, net.ServiceError('listenbrainz', 401, 'token needed'))
    worker.advance(cfg, list_id)
    assert 'HTTP 401: token needed' in lists.get(list_id)['error']


def test_title_and_description_fit_youtubes_limits():
    assert playlists.title(['Portishead', '60s <Motown>']) == 'Like Portishead, 60s Motown'
    long = playlists.title([f'artist number {n}' for n in range(40)])
    assert len(long) <= playlists.TITLE_CHARS and long.endswith('...')
    text = playlists.description([(f'line {n}', ['x' * 300] * 5) for n in range(20)])
    assert len(text.encode()) <= playlists.DESCRIPTION_BYTES
    assert text.startswith('Songs like each of these')


def with_videos(list_id, ids):
    whole = lists.get(list_id)
    picks = [p for line in whole['lines'] for p in line['picks']]
    for pick, vid in zip(picks, ids):
        videos.save(pick['recording_mbid'], 'found', ['open'], vid, 'musicbrainz',
                    {'title': 't', 'channel': 'c', 'duration_s': 1})


def test_a_playlist_past_the_budget_gets_the_rest_later(monkeypatch, cfg):
    list_id = made_list([song('1', 'A', 'a1'), song('2', 'B', 'b1')], [song('3', 'C', 'c1')])
    with_videos(list_id, ['v1', 'v2', 'v3'])
    monkeypatch.setattr(google_auth, 'access_token', lambda cfg: 'token')
    made, added = [], []
    monkeypatch.setattr(youtube, 'create_playlist', lambda *a: made.append(a) or 'PL1')
    budget = {'left': 2}

    def add(token, playlist_id, video_id, position):
        if not budget['left']:
            raise net.OverBudget("YouTube's daily units", net.next_pacific_midnight())
        budget['left'] -= 1
        added.append((video_id, position))

    monkeypatch.setattr(youtube, 'add_to_playlist', add)
    monkeypatch.setattr(youtube, 'playlist_videos', lambda token, pid: [v for v, _ in added])
    playlists.start(list_id, 'unlisted')
    worker.save(cfg, list_id)
    first = lists.get(list_id)['playlist']
    assert (first['step'], first['added'], first['url']) == ('saving', 2, 'https://www.youtube.com/playlist?list=PL1')
    assert first['wait_until']
    budget['left'] = 5
    with store.db() as con:
        con.execute('UPDATE playlists SET wait_until = NULL')
    worker.save(cfg, list_id)
    done = lists.get(list_id)['playlist']
    assert (done['step'], done['added']) == ('saved', 3)
    assert added == [('v1', 0), ('v2', 1), ('v3', 2)]
    assert len(made) == 1 and made[0][3] == 'unlisted'


def test_a_playlist_deleted_on_youtube_is_said_so(monkeypatch, cfg):
    list_id = made_list([song('1', 'A', 'a1')])
    with_videos(list_id, ['v1'])
    playlists.start(list_id, 'private')
    with store.db() as con:
        con.execute("UPDATE playlists SET youtube_id = 'PLgone'")
    monkeypatch.setattr(google_auth, 'access_token', lambda cfg: 'token')
    monkeypatch.setattr(youtube, 'playlist_videos', lambda token, pid: None)
    worker.save(cfg, list_id)
    assert 'deleted there' in lists.get(list_id)['playlist']['error']


def test_visibility_must_be_youtubes():
    list_id = made_list([song('1', 'A', 'a1')])
    with pytest.raises(ValueError):
        playlists.start(list_id, 'friends')


def test_a_playlist_link_unchecked_for_30_days_is_dropped(monkeypatch, cfg):
    fresh, old = made_list([song('1', 'A', 'a1')]), made_list([song('2', 'B', 'b1')])
    for list_id, days in ((fresh, 26), (old, 31)):
        playlists.start(list_id, 'unlisted')
        with store.db() as con:
            con.execute("UPDATE playlists SET step = 'saved', youtube_id = ?, yt_checked = ? WHERE list_id = ?",
                        (f'PL{list_id}', store.iso(net.now() - timedelta(days=days)), list_id))

    def down(*args, **kwargs):
        raise net.Unreachable('down')

    monkeypatch.setattr(youtube, 'playlist_exists', down)
    assert worker.refresh_playlists(cfg) == {'checked': 0, 'forgotten': 1}
    assert lists.get(fresh)['playlist']['youtube_id'] == f'PL{fresh}'
    gone = lists.get(old)['playlist']
    assert gone['youtube_id'] is None and 'YouTube library' in gone['error']
