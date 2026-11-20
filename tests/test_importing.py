import json

import pytest

from yt_playlist_maker import importing


def test_extract_video_and_playlist_ids():
    assert importing.extract_video_id('dQw4w9WgXcQ') == 'dQw4w9WgXcQ'
    assert importing.extract_video_id('https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=12') == 'dQw4w9WgXcQ'
    assert importing.extract_video_id('https://youtu.be/dQw4w9WgXcQ?si=abc') == 'dQw4w9WgXcQ'
    assert importing.extract_video_id('https://music.youtube.com/watch?v=dQw4w9WgXcQ') == 'dQw4w9WgXcQ'
    assert importing.extract_video_id('https://www.youtube.com/shorts/dQw4w9WgXcQ') == 'dQw4w9WgXcQ'
    assert importing.extract_video_id('Nina Simone') is None

    assert importing.extract_playlist_id('PL1234567890abcdef') == 'PL1234567890abcdef'
    assert importing.extract_playlist_id('https://www.youtube.com/playlist?list=PL1234567890abcdef') == 'PL1234567890abcdef'
    assert importing.extract_playlist_id('not-a-playlist') is None


def test_parse_voice_wei_library_json():
    payload = {
        'format': 'voice-wei-library',
        'version': 1,
        'title': 'Car Favorites',
        'songs': [
            {
                'videoId': 'abc12345678',
                'name': 'Roads',
                'artist': 'Portishead',
                'year': '1994',
                'durationSeconds': 303,
            },
            {
                'videoId': 'abc12345678',
                'name': 'Roads duplicate',
                'artist': 'Portishead',
            },
        ],
        'favorites': {
            'def87654321': {
                'videoId': 'def87654321',
                'name': 'Sinnerman',
                'artist': 'Nina Simone',
                'year': 1965,
                'favoritedAt': 1700000000000,
            },
        },
    }
    parsed = importing.parse(json.dumps(payload))
    assert parsed['format'] == 'voice-wei-library'
    assert parsed['title'] == 'Car Favorites'
    assert [(s['video_id'], s['title'], s['artist'], s['year'], s['length_ms']) for s in parsed['songs']] == [
        ('abc12345678', 'Roads', 'Portishead', 1994, 303000),
        ('def87654321', 'Sinnerman', 'Nina Simone', 1965, None),
    ]


def test_parse_mixed_urls_playlists_and_text_lines():
    raw = '\n'.join([
        '# comment ignored',
        'https://www.youtube.com/watch?v=abc12345678 # Portishead - Roads',
        'https://youtu.be/def87654321',
        'https://www.youtube.com/playlist?list=PL1234567890abcdef',
        'Nina Simone - Feeling Good',
        'https://www.youtube.com/watch?v=abc12345678',
    ])
    parsed = importing.parse(raw)
    assert parsed['playlist_ids'] == ['PL1234567890abcdef']
    assert [(s['video_id'], s['artist'], s['title'], s['source']) for s in parsed['songs']] == [
        ('abc12345678', 'Portishead', 'Roads', 'url'),
        ('def87654321', '', 'def87654321', 'url'),
        ('', 'Nina Simone', 'Feeling Good', 'text'),
    ]


def test_parse_rejects_empty_and_over_limit():
    with pytest.raises(ValueError, match='Paste a song list'):
        importing.parse('   \n  ')
    with pytest.raises(ValueError, match='At most'):
        importing.parse('\n'.join(f'Artist {n} - Song {n}' for n in range(importing.MAX_DIRECT_SONGS + 1)))
