"""Wikidata's YouTube video ids (P1651) for the recordings coverage.py looked up in MusicBrainz."""
import json
import time
import urllib.parse
import urllib.request

from probe import USER_AGENT, OUT

mb = json.loads((OUT / 'coverage-mb.json').read_text())
works = {mbid: [rel['work']['id'] for rel in rec.get('relations', []) if rel.get('target-type') == 'work']
         for mbid, rec in mb.items()}


def sparql(name, prop, keys):
    values = ' '.join(f'"{key}"' for key in keys)
    query = f'SELECT ?key ?video WHERE {{ VALUES ?key {{ {values} }} ?item wdt:{prop} ?key ; wdt:P1651 ?video . }}'
    data = urllib.parse.urlencode({'query': query, 'format': 'json'}).encode()
    request = urllib.request.Request('https://query.wikidata.org/sparql', data=data, headers={
        'User-Agent': USER_AGENT, 'Accept': 'application/sparql-results+json'})
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=90) as response:
        body = json.loads(response.read())
    (OUT / f'{name}.json').write_text(json.dumps(body))
    rows = body['results']['bindings']
    print(f'{name}: {len(rows)} rows in {time.monotonic() - started:.1f} s')
    return {row['key']['value'] for row in rows}


by_recording = sparql('coverage-wd-recording', 'P4404', list(mb))
time.sleep(2)
by_work = sparql('coverage-wd-work', 'P435', [w for ws in works.values() for w in ws])
found = by_recording | {mbid for mbid, ws in works.items() if set(ws) & by_work}
print(f'Wikidata: {len(by_recording)} by recording, {len(found)} by recording or work, of {len(mb)}')
