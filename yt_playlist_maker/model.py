"""The local language model, through matthoom's Ollama, holding the GPU through the gate (matthoom docs/GPU.md):
alone, as Ollama's models fill what they find, in the lane now, and only until the answer is in."""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

OLLAMA = 'http://127.0.0.1:11434'
GATE = Path.home() / 'proj' / 'matthoom' / 'core' / 'utils' / 'gpu.py'
WHO = 'yt-playlist-maker'
# matthoom's OLLAMA_LEAST_GB (core/language_models/client.py): with less room than this a model there is not loaded.
LEAST_GB = 12
GATE_WAIT_SECONDS = 120
REPLY_SECONDS = 600
UNLOAD_SECONDS = 60

_gate = None


class Unavailable(RuntimeError):
    """The model cannot answer now: the GPU is busy, or Ollama is not running. Not the list's fault."""


class BadAnswer(RuntimeError):
    """The model answered with something that is not the schema."""


def gate():
    """matthoom's gate, loaded by its path as docs/GPU.md asks of other projects, so no other matthoom module loads."""
    global _gate
    if _gate is None:
        if not GATE.exists():
            raise Unavailable(f'The GPU gate is not at {GATE}')
        spec = importlib.util.spec_from_file_location('gpu', GATE)
        module = importlib.util.module_from_spec(spec)
        sys.modules['gpu'] = module  # its dataclasses look the module up while it loads
        spec.loader.exec_module(module)
        module.configure(WHO, 'now', minutes=10, wait=GATE_WAIT_SECONDS)
        _gate = module
    return _gate


def ask(model: str, instructions: str, text: str, schema: dict) -> dict:
    """The model's answer, an object of the schema."""
    try:
        tags = requests.get(f'{OLLAMA}/api/tags', timeout=5)
        tags.raise_for_status()
    except requests.RequestException as exc:
        raise Unavailable(f'The language model server is not running ({OLLAMA}: {type(exc).__name__})') from exc
    names = {m.get('name') for m in tags.json().get('models', [])}
    if model not in names and f'{model}:latest' not in names:
        raise Unavailable(f'{model} is not downloaded in Ollama')
    gpu = gate()
    what = f'{model} in Ollama'
    try:
        gpu.hold(what, LEAST_GB, alone=True, via='ollama')
    except gpu.Busy as exc:
        raise Unavailable(str(exc)) from exc
    try:
        reply = requests.post(f'{OLLAMA}/api/chat', timeout=(10, REPLY_SECONDS), json={
            'model': model,
            'stream': False,
            'think': False,
            'format': schema,
            'keep_alive': '1m',
            'options': {'temperature': 0.1},
            'messages': [{'role': 'system', 'content': instructions}, {'role': 'user', 'content': text}],
        })
        reply.raise_for_status()
        body = reply.json()
    except requests.RequestException as exc:
        raise Unavailable(f'{model}: {type(exc).__name__}: {exc}') from exc
    finally:
        _unload(model)
        gpu.let_go(what)
    if body.get('done_reason') == 'length':
        raise BadAnswer(f'{model} was cut off at its length limit')
    content = body.get('message', {}).get('content', '')
    try:
        data = json.loads(content)
    except ValueError as exc:
        raise BadAnswer(f'{model} answered something other than JSON: {exc}') from exc
    if not isinstance(data, dict):
        raise BadAnswer(f'{model} answered JSON that is not an object')
    return data


def _unload(model: str) -> None:
    """Ask Ollama to unload the model, and wait until it has, so the GPU is free when the gate hears it is. Should
    this fail, Ollama unloads it by itself after the request's keep_alive of a minute."""
    try:
        requests.post(f'{OLLAMA}/api/generate', json={'model': model, 'keep_alive': 0}, timeout=30)
        deadline = time.monotonic() + UNLOAD_SECONDS
        while time.monotonic() < deadline:
            loaded = requests.get(f'{OLLAMA}/api/ps', timeout=5).json().get('models', [])
            if not any(m.get('name') == model or m.get('model') == model for m in loaded):
                return
            time.sleep(1)
        logger.warning('%s is still loaded %s s after asking Ollama to unload it', model, UNLOAD_SECONDS)
    except requests.RequestException as exc:
        logger.warning('Could not ask Ollama to unload %s: %s', model, exc)
