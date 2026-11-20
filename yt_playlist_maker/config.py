"""config.local.json: the port, his ListenBrainz token, his Google credentials, the language model, and optional
encrypted library sync settings. Gitignored; made from config.example.json the first time. A file that is not
that shape stops the app at once."""
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
CONFIG = ROOT / 'config.local.json'
EXAMPLE = ROOT / 'config.example.json'

REQUIRED_KEYS = frozenset({
    'port',
    'listenbrainz_token',
    'youtube_api_key',
    'google_client_id',
    'google_client_secret',
    'language_model',
})
SYNC_VISIBILITIES = ('unlisted', 'private', 'public')
INT_KEYS = frozenset({'port', 'sync_minutes'})


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    port: int
    listenbrainz_token: str
    youtube_api_key: str
    google_client_id: str
    google_client_secret: str
    language_model: str
    sync_url: str = ''
    sync_key: str = ''
    sync_playlist_id: str = ''
    sync_title: str = 'Favorite Songs'
    sync_visibility: str = 'unlisted'
    sync_minutes: int = 15


def load(path: Path = CONFIG) -> Config:
    if not path.exists():
        shutil.copyfile(EXAMPLE, path)
    try:
        raw = json.loads(path.read_text())
    except ValueError as exc:
        raise ConfigError(f'{path} is not JSON: {exc}') from exc
    if not isinstance(raw, dict):
        raise ConfigError(f'{path} must be a JSON object')
    allowed = set(Config.__dataclass_fields__)
    got = set(raw)
    missing = sorted(REQUIRED_KEYS - got)
    unknown = sorted(got - allowed)
    if missing or unknown:
        raise ConfigError(f'{path} has wrong keys; missing {missing}, unknown {unknown}')
    for key in INT_KEYS:
        if key in raw and (isinstance(raw[key], bool) or not isinstance(raw[key], int)):
            raise ConfigError(f'{path}: {key} must be a number')
    for key in got - INT_KEYS:
        if not isinstance(raw[key], str):
            raise ConfigError(f'{path}: {key} must be text')
    if not raw['language_model']:
        raise ConfigError(f'{path}: language_model must name an Ollama model')
    cfg = Config(**raw)
    if cfg.sync_visibility not in SYNC_VISIBILITIES:
        raise ConfigError(f'{path}: sync_visibility must be one of {SYNC_VISIBILITIES}')
    if cfg.sync_minutes < 1:
        raise ConfigError(f'{path}: sync_minutes must be at least 1')
    if cfg.sync_url and not cfg.sync_url.startswith(('http://', 'https://')):
        raise ConfigError(f'{path}: sync_url must be an http or https URL')
    if cfg.sync_key and len(cfg.sync_key.strip()) < 16:
        raise ConfigError(f'{path}: sync_key must be at least 16 characters')
    return cfg
