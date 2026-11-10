"""Read sample lines with a local model, to see what it makes of each kind and how long it takes."""
import json
import sys
import time

from yt_playlist_maker import model, reading

LINES = [
    'Nina Simone',
    'Portishead',
    '80s synth pop',
    '1960s Motown',
    'late Beatles',
    'Genesis',
    'something for a rainy sunday morning with coffee',
    'brazilian bossa nova',
]

if __name__ == '__main__':
    name = sys.argv[1] if len(sys.argv) > 1 else 'gemma4:31b'
    numbered = '\n'.join(f'{n}. {t}' for n, t in enumerate(LINES, 1))
    started = time.monotonic()
    answer = model.ask(name, reading.INSTRUCTIONS, numbered, reading.SCHEMA)
    print(f'{name}: {time.monotonic() - started:.1f} s')
    for text, parsed in zip(LINES, reading.parse(answer, len(LINES))):
        print(f'{text!r}: {json.dumps(parsed, ensure_ascii=False)}')
