"""How each line is read (SPEC.md, "How songs are chosen", 1): the local model says what the line is, and each
artist it names is looked up in MusicBrainz. A line starting 'artist:' is that artist, without the model."""
from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime

from . import listenbrainz, model, musicbrainz

KINDS = ('artist', 'style', 'description')
MAX_EXAMPLE_ARTISTS = 5
MAX_TAGS = 3
CLOSE_SCORE = 90
ARTIST_PREFIX = re.compile(r'^\s*artist\s*:\s*(.+?)\s*$', re.IGNORECASE)

INSTRUCTIONS = """You read a list someone typed to make a music playlist. Each line names a singer, band or \
composer, an era, a genre or scene, or describes music in words. For each line, in order, give:

- kind: "artist" when the line names one singer, band or composer, perhaps with a period of their career; "style" \
when it names a genre, scene, era or decade; "description" for anything else.
- artists: for an artist line, that one artist's name, spelled as MusicBrainz spells it. For a style or description \
line, up to 5 well-known artists who best represent it.
- tags: up to 3 lowercase MusicBrainz genre tags that fit the line, most specific first, for example "trip hop", \
"motown", "synth-pop", "bossa nova", "delta blues". For an artist line, give tags only when the line itself names \
a genre.
- years: the first and last year the line means, when it means a period: "80s" is 1980 to 1989, "late Beatles" is \
1966 to 1970. Otherwise null.

Answer only with JSON of the given schema, one entry per line, with its line number."""

SCHEMA = {
    'type': 'object',
    'properties': {
        'lines': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'line': {'type': 'integer'},
                    'kind': {'type': 'string', 'enum': list(KINDS)},
                    'artists': {'type': 'array', 'items': {'type': 'string'}},
                    'tags': {'type': 'array', 'items': {'type': 'string'}},
                    'years': {'anyOf': [
                        {'type': 'null'},
                        {'type': 'object', 'properties': {'from': {'type': 'integer'}, 'to': {'type': 'integer'}},
                         'required': ['from', 'to']},
                    ]},
                },
                'required': ['line', 'kind', 'artists', 'tags', 'years'],
            },
        },
    },
    'required': ['lines'],
}


def fold(text: str) -> str:
    """A name or title for comparing: no accents, case or punctuation, one space between words."""
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c)).casefold()
    return ' '.join(re.sub(r'[^\w]+', ' ', text).split())


def read(texts: list[str], model_name: str) -> list[dict]:
    """A reading of each line: kind, by, artists (looked up), tags, years."""
    readings: list[dict | None] = [None] * len(texts)
    asked = []
    for i, text in enumerate(texts):
        prefixed = ARTIST_PREFIX.match(text)
        if prefixed:
            readings[i] = {'kind': 'artist', 'by': 'you', 'artists': [prefixed.group(1)], 'tags': [], 'years': None}
        else:
            asked.append(i)
    if asked:
        numbered = '\n'.join(f'{n}. {texts[i]}' for n, i in enumerate(asked, 1))
        answer = model.ask(model_name, INSTRUCTIONS, numbered, SCHEMA)
        for i, reading in zip(asked, parse(answer, len(asked))):
            readings[i] = {**reading, 'by': model_name}
    for reading in readings:
        reading['artists'] = [find_artist(name) for name in reading['artists']]
    return readings


def parse(answer: dict, count: int) -> list[dict]:
    """The model's answer for count lines, checked and tidied; BadAnswer when it is not one entry per line."""
    entries = answer.get('lines')
    if not isinstance(entries, list) or len(entries) != count:
        raise model.BadAnswer(f'the model read {len(entries) if isinstance(entries, list) else 0} lines of {count}')
    by_line = {e.get('line'): e for e in entries if isinstance(e, dict)}
    if sorted(by_line) != list(range(1, count + 1)):
        raise model.BadAnswer('the model did not give one entry for each line number')
    this_year = datetime.now(UTC).year
    readings = []
    for n in range(1, count + 1):
        entry = by_line[n]
        kind = entry.get('kind')
        if kind not in KINDS:
            raise model.BadAnswer(f'line {n}: kind {kind!r}')
        artists = list(dict.fromkeys(a.strip() for a in entry.get('artists') or [] if isinstance(a, str) and a.strip()))
        artists = artists[:1] if kind == 'artist' else artists[:MAX_EXAMPLE_ARTISTS]
        if kind == 'artist' and not artists:
            raise model.BadAnswer(f'line {n} is an artist line without an artist')
        tags = list(dict.fromkeys(' '.join(t.lower().split()) for t in entry.get('tags') or []
                                  if isinstance(t, str) and t.strip()))[:MAX_TAGS]
        years = entry.get('years')
        span = None
        if isinstance(years, dict) and isinstance(years.get('from'), int) and isinstance(years.get('to'), int):
            start, end = years['from'], years['to']
            if 1000 <= start <= end <= this_year + 1:
                span = [start, end]
        readings.append({'kind': kind, 'artists': artists, 'tags': tags, 'years': span})
    return readings


def find_artist(name: str) -> dict:
    """MusicBrainz's artist for the name: of those whose name or alias is the name, the one most listened to on
    ListenBrainz, with the others offered; else the closest match, said so; else none."""
    found = musicbrainz.search_artists(name)
    wanted = fold(name)
    exact = [a for a in found if wanted in {fold(a['name']), fold(a['sort_name'])} | {fold(x) for x in a['aliases']}]
    note = ''
    if not exact:
        close = [a for a in found if a['score'] >= CLOSE_SCORE]
        if not close:
            return {'asked': name, 'mbid': None, 'name': name, 'comment': '', 'others': [],
                    'note': 'MusicBrainz has no artist of that name'}
        exact, note = close[:1], 'the closest name in MusicBrainz'
    if len(exact) > 1:
        listens = listenbrainz.artist_listens([a['mbid'] for a in exact])
        exact.sort(key=lambda a: -listens.get(a['mbid'], 0))
    best = exact[0]
    return {'asked': name, 'mbid': best['mbid'], 'name': best['name'], 'comment': best['comment'], 'note': note,
            'others': [{'mbid': a['mbid'], 'name': a['name'], 'comment': a['comment']} for a in exact[1:6]]}
