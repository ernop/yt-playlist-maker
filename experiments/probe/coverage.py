"""How many popular recordings already have a YouTube video id in MusicBrainz or Wikidata?"""
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from probe import USER_AGENT, OUT

YOUTUBE_ID = re.compile(r'(?:youtube\.com/watch\?v=|youtu\.be/)([-_0-9A-Za-z]{11})')


def fetch(url, data=None, accept='application/json'):
    for attempt in range(4):
        request = urllib.request.Request(url, data=data, headers={'User-Agent': USER_AGENT, 'Accept': accept})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code != 503:
                raise
            wait = int(error.headers.get('Retry-After') or 5)
            print(f'  503, waiting {wait} s')
            time.sleep(wait)
    raise RuntimeError(f'still 503 after 4 tries: {url}')


recordings = {}
for path in sorted(OUT.glob('lbradio-artist-portishead-pop70-*.json')):
    for rows in json.loads(path.read_text()).values():
        for row in rows:
            recordings[row['recording_mbid']] = row['similar_artist_name']
print(f'{len(recordings)} recordings')

mb = {}
for n, mbid in enumerate(recordings, 1):
    mb[mbid] = fetch(f'https://musicbrainz.org/ws/2/recording/{mbid}?inc=url-rels+work-rels&fmt=json')
    time.sleep(2)
(OUT / 'coverage-mb.json').write_text(json.dumps(mb))

mb_youtube = {}
works = {}
for mbid, rec in mb.items():
    for rel in rec.get('relations', []):
        url = (rel.get('url') or {}).get('resource', '')
        match = YOUTUBE_ID.search(url)
        if match:
            mb_youtube[mbid] = match.group(1)
        if rel.get('target-type') == 'work':
            works.setdefault(mbid, []).append(rel['work']['id'])

values_rec = ' '.join(f'"{m}"' for m in recordings)
values_work = ' '.join(f'"{w}"' for ws in works.values() for w in ws)
sparql = f'''
SELECT ?key ?kind ?video WHERE {{
  {{ VALUES ?key {{ {values_rec} }} ?item wdt:P4404 ?key . BIND("recording" AS ?kind) }}
  UNION
  {{ VALUES ?key {{ {values_work} }} ?item wdt:P435 ?key . BIND("work" AS ?kind) }}
  ?item wdt:P1651 ?video .
}}'''
data = urllib.parse.urlencode({'query': sparql, 'format': 'json'}).encode()
wikidata = fetch('https://query.wikidata.org/sparql', data=data, accept='application/sparql-results+json')
(OUT / 'coverage-wikidata.json').write_text(json.dumps(wikidata))

wd_by_recording = set()
work_to_recording = {w: r for r, ws in works.items() for w in ws}
for row in wikidata['results']['bindings']:
    key = row['key']['value']
    wd_by_recording.add(key if row['kind']['value'] == 'recording' else work_to_recording[key])

either = set(mb_youtube) | wd_by_recording
print(f'MusicBrainz recording links: {len(mb_youtube)} of {len(recordings)}')
print(f'recordings with a work: {len(works)}')
print(f'Wikidata (by recording or work): {len(wd_by_recording)} of {len(recordings)}')
print(f'either: {len(either)} of {len(recordings)}')
