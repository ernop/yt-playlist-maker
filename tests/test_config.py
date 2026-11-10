import json

import pytest

from yt_playlist_maker import config


def test_first_run_writes_the_example(tmp_path):
    path = tmp_path / 'config.local.json'
    loaded = config.load(path)
    assert json.loads(path.read_text()) == json.loads(config.EXAMPLE.read_text())
    assert loaded.port == 8020
    assert loaded.language_model


@pytest.mark.parametrize('change, words', [
    ({'extra': 1}, 'unknown'),
    ({'port': '8020'}, 'port must be a number'),
    ({'youtube_api_key': None}, 'must be text'),
    ({'language_model': ''}, 'must name an Ollama model'),
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
