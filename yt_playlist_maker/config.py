"""config.local.json: the port, his ListenBrainz token, his Google credentials and the language model.
Gitignored; made from config.example.json the first time. A file that is not exactly that shape stops the app at
once."""
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
CONFIG = ROOT / 'config.local.json'
EXAMPLE = ROOT / 'config.example.json'


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


def load(path: Path = CONFIG) -> Config:
    if not path.exists():
        shutil.copyfile(EXAMPLE, path)
    try:
        raw = json.loads(path.read_text())
    except ValueError as exc:
        raise ConfigError(f'{path} is not JSON: {exc}') from exc
    expected = set(Config.__dataclass_fields__)
    if not isinstance(raw, dict) or set(raw) != expected:
        got = set(raw) if isinstance(raw, dict) else set()
        raise ConfigError(f'{path} must have exactly the keys {sorted(expected)}; '
                          f'missing {sorted(expected - got)}, unknown {sorted(got - expected)}')
    if not isinstance(raw['port'], int):
        raise ConfigError(f'{path}: port must be a number')
    for key in expected - {'port'}:
        if not isinstance(raw[key], str):
            raise ConfigError(f'{path}: {key} must be text')
    if not raw['language_model']:
        raise ConfigError(f'{path}: language_model must name an Ollama model')
    return Config(**raw)
