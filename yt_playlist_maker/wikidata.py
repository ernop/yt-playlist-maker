"""Wikidata's Query Service: the YouTube video ids (P1651) on the items of MusicBrainz works (P435)."""
from __future__ import annotations

import re

from . import net

SPARQL = 'https://query.wikidata.org/sparql'
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
VIDEO_ID = re.compile(r'^[-_0-9A-Za-z]{11}$')
BATCH = 200


def videos_for_works(work_mbids: list[str]) -> dict[str, list[str]]:
    """Each work's YouTube video ids, by work mbid."""
    works = sorted({w for w in work_mbids if UUID.match(w)})
    found: dict[str, list[str]] = {}
    for start in range(0, len(works), BATCH):
        values = ' '.join(f'"{w}"' for w in works[start:start + BATCH])
        query = f'SELECT ?work ?video WHERE {{ VALUES ?work {{ {values} }} ?item wdt:P435 ?work ; wdt:P1651 ?video . }}'
        data = net.call('wikidata', 'POST', SPARQL, form={'query': query, 'format': 'json'},
                        headers={'Accept': 'application/sparql-results+json'})
        for row in data['results']['bindings']:
            video = row['video']['value']
            if VIDEO_ID.match(video):
                found.setdefault(row['work']['value'], []).append(video)
    return found
