"""Signing in to his YouTube account, only to make playlists: Google's installed-app flow, its answer served on
127.0.0.1, with PKCE (RFC 8252, RFC 7636). The token is kept in data/google_token.json, readable by him only."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse

from . import net
from .config import DATA, Config

AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN = 'https://oauth2.googleapis.com/token'
REVOKE = 'https://oauth2.googleapis.com/revoke'
SCOPE = 'https://www.googleapis.com/auth/youtube'
PENDING_SECONDS = 900

_token_file = DATA / 'google_token.json'
_pending: dict[str, tuple[str, str, float]] = {}
_lock = threading.Lock()


class SignInNeeded(RuntimeError):
    pass


def use(path) -> None:
    """Keep the token elsewhere (tests)."""
    global _token_file
    _token_file = path


def redirect_uri(cfg: Config) -> str:
    return f'http://127.0.0.1:{cfg.port}/google/callback'


def start(cfg: Config) -> tuple[str, str]:
    """Google's sign-in page for this app, asking for the youtube scope, and the state that comes back with it."""
    if not (cfg.google_client_id and cfg.google_client_secret):
        raise SignInNeeded('config.local.json has no google_client_id and google_client_secret '
                           '(docs/SETUP.md)')
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    with _lock:
        now = time.monotonic()
        for old in [s for s, (_, _, t) in _pending.items() if now - t > PENDING_SECONDS]:
            del _pending[old]
        _pending[state] = (verifier, redirect_uri(cfg), now)
    return state, AUTH + '?' + urllib.parse.urlencode({
        'client_id': cfg.google_client_id,
        'redirect_uri': redirect_uri(cfg),
        'response_type': 'code',
        'scope': SCOPE,
        'access_type': 'offline',
        'prompt': 'consent',
        'state': state,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    })


def finish(cfg: Config, state: str, code: str) -> None:
    with _lock:
        pending = _pending.pop(state, None)
    if pending is None:
        raise SignInNeeded('this sign-in was not started here, or is more than 15 minutes old: sign in again')
    verifier, redirect, _ = pending
    data = net.call('google', 'POST', TOKEN, keep=False, form={
        'client_id': cfg.google_client_id,
        'client_secret': cfg.google_client_secret,
        'code': code,
        'code_verifier': verifier,
        'grant_type': 'authorization_code',
        'redirect_uri': redirect,
    })
    if SCOPE not in data.get('scope', '').split():
        raise SignInNeeded('Google did not grant the youtube scope: sign in again and allow it')
    if not data.get('refresh_token'):
        raise SignInNeeded('Google gave no refresh token: sign in again')
    _save({'refresh_token': data['refresh_token'], 'access_token': data['access_token'],
           'expires_at': time.time() + int(data.get('expires_in', 0))})


def signed_in() -> bool:
    return _token_file.exists()


def access_token(cfg: Config) -> str:
    """A current access token, refreshed when it is within a minute of its end."""
    if not _token_file.exists():
        raise SignInNeeded('Sign in to YouTube first')
    token = json.loads(_token_file.read_text())
    if token.get('expires_at', 0) - 60 > time.time():
        return token['access_token']
    try:
        data = net.call('google', 'POST', TOKEN, keep=False, form={
            'client_id': cfg.google_client_id,
            'client_secret': cfg.google_client_secret,
            'refresh_token': token['refresh_token'],
            'grant_type': 'refresh_token',
        })
    except net.ServiceError as exc:
        if exc.status in (400, 401) and 'invalid_grant' in exc.detail:
            _token_file.unlink(missing_ok=True)
            raise SignInNeeded('Google ended the sign-in (in Testing it lasts 7 days): sign in to YouTube again') from exc
        raise
    token.update(access_token=data['access_token'], expires_at=time.time() + int(data.get('expires_in', 0)))
    _save(token)
    return token['access_token']


def sign_out() -> None:
    """Revoke the sign-in at Google and delete the token."""
    if not _token_file.exists():
        return
    token = json.loads(_token_file.read_text())
    try:
        net.call('google', 'POST', REVOKE, keep=False, form={'token': token['refresh_token']})
    finally:
        _token_file.unlink(missing_ok=True)


def _save(token: dict) -> None:
    _token_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = _token_file.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(token, handle)
    os.replace(tmp, _token_file)
