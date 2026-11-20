import json

import pytest

from yt_playlist_maker import config


def test_first_run_writes_the_example(tmp_path):
    path = tmp_path / 'config.local.json'
    loaded = config.load(path)
    assert json.loads(path.read_text()) == json.loads(config.EXAMPLE.read_text())
    assert loaded.port == 8020
    assert loaded.language_model
    assert loaded.sync_url == ''
    assert loaded.sync_minutes == 15


def test_legacy_six_key_config_loads_with_sync_defaults(tmp_path):
    path = tmp_path / 'config.local.json'
    path.write_text(json.dumps({
        'port': 8020,
        'listenbrainz_token': 'lb',
        'youtube_api_key': 'yt',
        'google_client_id': 'cid',
        'google_client_secret': 'sec',
        'language_model': 'gemma4:31b',
    }))
    loaded = config.load(path)
    assert loaded.port == 8020
    assert loaded.sync_url == ''
    assert loaded.sync_key == ''
    assert loaded.sync_visibility == 'unlisted'
    assert loaded.sync_minutes == 15


@pytest.mark.parametrize('change, words', [
    ({'extra': 1}, 'unknown'),
    ({'port': '8020'}, 'port must be a number'),
    ({'port': True}, 'port must be a number'),
    ({'youtube_api_key': None}, 'must be text'),
    ({'language_model': ''}, 'must name an Ollama model'),
    ({'sync_visibility': 'friends'}, 'sync_visibility'),
    ({'sync_minutes': 0}, 'sync_minutes'),
    ({'sync_url': 'ftp://example.com/proxy.php'}, 'sync_url'),
    ({'sync_key': 'too-short'}, 'at least 16'),
])
def test_a_wrong_file_stops_the_app(tmp_path, change, words):
    raw = {**json.loads(config.EXAMPLE.read_text()), **change}
    path = tmp_path / 'config.local.json'
    path.write_text(json.dumps(raw))
    with pytest.raises(config.ConfigError, match=words):
        config.load(path)


def test_a_missing_key_is_named(tmp_path):
    raw = json.loads(config.EXAMPLE.read_text())
    del raw['google_client_id']
    path = tmp_path / 'config.local.json'
    path.write_text(json.dumps(raw))
    with pytest.raises(config.ConfigError, match="missing \\['google_client_id'\\]"):
        config.load(path)
