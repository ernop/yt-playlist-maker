"""Ask each music service a few questions and keep the answers, to learn their shapes."""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = 'yt-playlist-maker/0.1 ( https://github.com/ernop/yt-playlist-maker )'
OUT = Path(__file__).parent / 'out'
OUT.mkdir(exist_ok=True)


def get(name, url, accept='application/json'):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': accept})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            headers = {k: v for k, v in response.headers.items() if k.lower().startswith('x-ratelimit')}
            status = response.status
    except urllib.error.HTTPError as error:
        body = error.read()
        headers = {}
        status = error.code
    seconds = time.monotonic() - started
    (OUT / f'{name}.json').write_bytes(body)
    print(f'{name}: HTTP {status}, {len(body)} bytes, {seconds:.1f} s, {headers}')
    time.sleep(2)
    return json.loads(body) if status == 200 else None


def lb_radio(name, prompt, mode='easy'):
    query = urllib.parse.urlencode({'prompt': prompt, 'mode': mode})
    return get(name, f'https://api.listenbrainz.org/1/explore/lb-radio?{query}')


if __name__ == '__main__':
    which = sys.argv[1]
    if which == 'radio':
        lb_radio('radio-portishead', 'artist:(Portishead)')
        lb_radio('radio-tag-1980s', 'tag:(1980s)')
        lb_radio('radio-tag-synthpop', 'tag:(synth-pop)')
    elif which == 'lb':
        portishead = '8f6bd1e4-fbe1-4f50-aa9b-94c450ec0f11'
        get('lb-top-portishead', f'https://api.listenbrainz.org/1/popularity/top-recordings-for-artist/{portishead}')
        query = urllib.parse.urlencode({
            'artist_mbids': portishead,
            'algorithm': 'session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30',
        })
        get('lb-similar-portishead', f'https://labs.api.listenbrainz.org/similar-artists/json?{query}')
        get('lb-metadata-recording', 'https://api.listenbrainz.org/1/metadata/recording/?'
            'recording_mbids=13dd61c7-ce73-4e97-9f0c-9f0e53144411&inc=artist+tag+release')
    elif which == 'lbradio':
        portishead = '8f6bd1e4-fbe1-4f50-aa9b-94c450ec0f11'
        query = urllib.parse.urlencode({'mode': 'easy', 'max_similar_artists': 5, 'max_recordings_per_artist': 2,
                                        'pop_begin': 0, 'pop_end': 100})
        get('lbradio-artist-portishead', f'https://api.listenbrainz.org/1/lb-radio/artist/{portishead}?{query}')
        query = urllib.parse.urlencode({'tag': 'synth-pop', 'operator': 'and', 'pop_begin': 0, 'pop_end': 100,
                                        'count': 20})
        get('lbradio-tags-synthpop', f'https://api.listenbrainz.org/1/lb-radio/tags?{query}')
    elif which == 'lbradio2':
        portishead = '8f6bd1e4-fbe1-4f50-aa9b-94c450ec0f11'
        query = urllib.parse.urlencode({'mode': 'easy', 'max_similar_artists': 10, 'max_recordings_per_artist': 3,
                                        'pop_begin': 70, 'pop_end': 100})
        for n in (1, 2):
            get(f'lbradio-artist-portishead-pop70-{n}', f'https://api.listenbrainz.org/1/lb-radio/artist/{portishead}?{query}')
        for tag in ('1980s', '80s', 'motown'):
            query = urllib.parse.urlencode({'tag': tag, 'operator': 'and', 'pop_begin': 50, 'pop_end': 100, 'count': 50})
            get(f'lbradio-tags-{tag}', f'https://api.listenbrainz.org/1/lb-radio/tags?{query}')
        get('labs-index', 'https://labs.api.listenbrainz.org/', accept='text/html')
    elif which == 'mb':
        query = urllib.parse.urlencode({'query': 'artist:"Nina Simone"', 'fmt': 'json', 'limit': 5})
        get('mb-artist-nina', f'https://musicbrainz.org/ws/2/artist?{query}')
        query = urllib.parse.urlencode({'query': 'artist:"Genesis"', 'fmt': 'json', 'limit': 5})
        get('mb-artist-genesis', f'https://musicbrainz.org/ws/2/artist?{query}')
