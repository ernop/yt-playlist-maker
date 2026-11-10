# yt-playlist-maker: start here

He types a list of singers, bands, eras or any words; the app picks the 2 to 5 songs most like each line from
ListenBrainz and MusicBrainz, finds their YouTube videos, and makes one YouTube playlist he can send as a link.
It runs on his computer only, at http://yt-playlist-maker.localhost (127.0.0.1:8020). **Public repository**:
nothing personal is committed; `data/` and `config.local.json` are gitignored.

## Read first

| Doc | What it holds |
|-|-|
| [SPEC.md](SPEC.md) | **Authoritative.** His words, what he sees, how songs and videos are chosen, every outside service with its limit and budget, the decisions and the code map |
| [docs/SETUP.md](docs/SETUP.md) | The ListenBrainz token, and the Google Cloud project: the YouTube key and the sign-in client |
| [PRIVACY.md](PRIVACY.md) | What the app keeps and sends, as YouTube's policies require of every API client |
| [README.md](README.md) | Running it |

Every requirement or decision he gives goes into SPEC.md in the same task, with his words and the date.

## Rules

- **Outside services only through `yt_playlist_maker/net.py`**: its User-Agent, one request per interval to each
  service, YouTube's budgets at half the published limits, the kept answers and the backoff. Work out the worst
  case of a change before adding a request (SPEC.md, "Outside services").
- **YouTube's policies**: its data is kept at most 30 days (`worker.upkeep`); no scraping, so no `yt-dlp` search;
  nothing is written to his account except on his press of the button, with the visibility shown first.
- **Never represent him**: no request names him beyond the public repository's address; never sign up, post or
  send anything as him. Ask before any new identity, account or token.
- **Above-board only**: the documented way a service offers; never route around a limit or a gate.
- **The language model is local** (Ollama on 127.0.0.1:11434) and holds the GPU only through matthoom's gate
  (`~/proj/matthoom/docs/GPU.md`); never an outside AI service.
- **Errors reach the page**: a step that fails says why in the list's state; no `except: pass`.
- **No environment variables**: settings are in `config.local.json` (made from `config.example.json`).
- **The page**: no gray text (neutral text is #000 on #fff), every color in `static/style.css` explained, rows on
  one line, the values more legible than their labels, widths fluid. `tests/test_style.py` checks the colors.
- Plain `'` and `"` in code; text is written with the exact technical word, no metaphor, no estimate.

## Run and test

```bash
uv sync
uv run pytest                                   # no network: services are faked
systemctl --user restart yt-playlist-maker      # after a code change
journalctl --user -u yt-playlist-maker -f
```

The unit is rendered from `~/proj/mybrowser/infrastructure/dev-app-services/yt-playlist-maker.service.template`
by that folder's installer; the Caddy route and the dashboard entry are in mybrowser's
`utilities/caddy/projects.json` and `machines/PC.json`. Record any change to them in mybrowser.

## Finishing

When a piece of work is done and `uv run pytest` passes, commit it and push it (github.com/ernop needs no
approval). Stage only your own changes and commit by naming the paths (`git commit -- <paths>`); read the diff
first, and check that nothing personal is in it.
