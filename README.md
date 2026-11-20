# yt-playlist-maker

Type a list, one line each: a singer, a band, an era, a genre, or any words ("something for a rainy Sunday
morning"), OR switch to **From a song list** to import exported JSON (`voice-wei-library` or generic), YouTube
video/playlist links, or `Artist - Song` lines directly into a new or existing YouTube playlist. Optional
client-side AES-256-GCM encrypted sync keeps `voice-wei` favorites on a phone automatically synced with a YouTube
playlist on this computer.

- Songs come from [ListenBrainz](https://listenbrainz.org) (similar artists, listen counts, tag radio) and
  [MusicBrainz](https://musicbrainz.org); a local language model through [Ollama](https://ollama.com) only reads
  the lines.
- Videos come from MusicBrainz's and [Wikidata](https://www.wikidata.org)'s links first, then the artist's own
  YouTube channel, then YouTube search, within half of YouTube's daily quota.
- It runs on one computer for one person: a Flask server on 127.0.0.1, SQLite, plain HTML and JavaScript.

How it decides, and why: [SPEC.md](SPEC.md). What it keeps and sends: [PRIVACY.md](PRIVACY.md).

## Run

Needs Python 3.12 or later, [uv](https://docs.astral.sh/uv/), Ollama with the model named in
`config.local.json` (`gemma4:31b` unless it says otherwise), and the GPU gate at
`~/proj/matthoom/core/utils/gpu.py`, which every program on the author's computer holds before it uses the GPU.

```bash
uv sync
uv run python -m yt_playlist_maker     # http://127.0.0.1:8020
```

The first run writes `config.local.json` from `config.example.json`. Choosing songs needs a ListenBrainz user
token; finding videos and making playlists need a Google Cloud project of your own: [docs/SETUP.md](docs/SETUP.md).

## Test

```bash
uv run pytest
```
