"""Every test gets its own database and token file, and no request leaves the computer: net's HTTP session is
replaced by a FakeSession, which answers from the routes a test gives it and fails on any other request."""
from __future__ import annotations

import dataclasses

import pytest
from helpers import FakeSession

from yt_playlist_maker import google_auth, listenbrainz, net, store
from yt_playlist_maker.config import Config


@pytest.fixture(autouse=True)
def session(tmp_path, monkeypatch) -> FakeSession:
    store.use(tmp_path / 'app.sqlite3')
    store.setup()
    google_auth.use(tmp_path / 'google_token.json')
    monkeypatch.setattr(listenbrainz, '_token', 'LBTOKEN')
    fake = FakeSession()
    monkeypatch.setattr(net, '_session', fake)
    monkeypatch.setattr(net, 'SERVICES', {k: dataclasses.replace(v, interval=0) for k, v in net.SERVICES.items()})
    monkeypatch.setattr(net, '_last', {})
    monkeypatch.setattr(net.time, 'sleep', fake.slept.append)
    return fake


@pytest.fixture
def cfg() -> Config:
    return Config(port=8020, listenbrainz_token='LBTOKEN', youtube_api_key='KEY', google_client_id='client',
                  google_client_secret='secret', language_model='test-model')
