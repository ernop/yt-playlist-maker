# yt-playlist-maker: what it is and why

He types a list, one line each: singers, bands, eras, any text. The app finds the 2 to 5 songs most like each
line and makes one YouTube playlist of them, which anyone he sends the link to can play. It runs on his computer
only.

## His words

- 2026-11-09: "user will input a list of singers, eras, bands, some text. we need to take that and fully output a
  youtube playlist of the most similar songs for each one, like, maybe 2-5 songs for each one, linked within
  youtube. how can we design software to do this, as a new, standalone app runnnig on my computer only?"
- 2026-11-09, on where the playlist lives: "Is there some way to genreate something which I can share, so other
  people will be able to play it, if i just send them the link? that's ideal"
- 2026-11-09, on the contact the music databases see: "whatever works best, really you're free here."
- 2026-11-09: "let's just make the entire thing, confirm it works, test it, then check it in. i think this belongs
  in a new repo - what do you think? yt-playlist-maker or similar? depending on how input works. you don't ahve to
  finalize this now, we can discuss it."
- 2026-11-10, asked how to get an artist's top songs now that ListenBrainz gates them behind a user token, he
  chose "Use a ListenBrainz token from my MetaBrainz account; I'll paste it into config.local.json" (decision 2).
- 2026-11-10, asked whether the repository is public or private, he chose public (decision 5).

## What he sees

One page, `http://yt-playlist-maker.localhost` (`127.0.0.1:8020`). Its state is in its URL (`/?list=<id>`).

1. **The list.** A box, one line each, at most 20 lines of 200 characters (the worst case below is sized on 20),
   and how many songs each line gets: 2, 3, 4 or 5 (3 at first). "Find songs" starts the work. A line starting
   `artist:` is taken as that artist without asking the language model. Repeated lines count once.
2. **How each line was read**, under the line: an artist (its MusicBrainz name and description), or tags and
   years, with the example artists the model named. An artist name that several MusicBrainz artists share is
   taken as the one most listened to on ListenBrainz; the others are offered beside it.
3. **The songs**, under their line, one row each: title, artist, year, length, why it was chosen ("like
   Portishead", "tagged motown"), and its video: found (and where), waiting for YouTube's daily budget (until
   midnight Pacific time), or none found. Each song can be swapped for the line's next candidate, or removed.
4. **Make the playlist on YouTube**: a new playlist in his YouTube account, **unlisted** unless he picks private or
   public. Unlisted means anyone with the link can play it, it is not in YouTube's search, and its page shows his
   channel's name. Then its link, to copy and send.
   Then **Add the new songs to the playlist**, after a swap, adds what is missing to the same playlist.
5. **Its state**, above the songs: the step it is on and its progress; a wait, with its reason and the time it
   tries again, and "Try now"; or the error that stopped it, and "Try again". "Read the lines again" asks the
   model again and chooses again.
6. **Earlier lists**, newest first, each with its playlist's link.
7. **Setup**: whether the ListenBrainz token, the YouTube key and the sign-in are there, what each is for, and
   how to get them (`docs/SETUP.md`); "Sign in to YouTube"; "Sign out and delete YouTube data", or, signed out,
   "Delete YouTube data".

The page credits its sources: ListenBrainz and MusicBrainz (song data, CC0), Wikidata (CC0), YouTube.

## How songs are chosen

All data is ListenBrainz's and MusicBrainz's, from their documented endpoints; the language model only reads the
lines, it never picks songs.

1. **Reading.** The local language model reads all lines at once and says, for each, its kind (`artist`, `style`
   or `description`), the artists it names, up to 3 MusicBrainz tags (lowercase genres, such as `trip hop` or
   `motown`) and a year range. A style or description gets up to 5 example artists. "Late Beatles" is the artist
   The Beatles with the years 1966 to 1970. Each artist name is then looked up in MusicBrainz.
2. **An artist line**: the artists most like it, in ListenBrainz's order of similarity (its similar-artists
   dataset, scored from listening sessions); from each in turn, its most-listened recording. The seed artist's own
   songs are left out: he knows them, and asked for similar ones.
3. **A style or description line**: the recordings ListenBrainz's tag radio gives for each tag, and the
   most-listened recordings of each example artist; only those first released within the years, when years were
   given; the most listened first.
4. **For every line**: one song per artist; no recording, and no song of the same name by the same artist, twice in
   the list; an artist already used by another line is skipped while others remain; no recording shorter than
   60 s (intros, interludes, skits). A recording of unknown length is kept, and its video is then matched by
   title alone. Each line keeps its next 10 candidates, for Swap.

## How a song finds its video

Cheapest first; each step only for the songs still without one:

1. What the app already knows.
2. **Open data**: the recording's YouTube link in MusicBrainz, or the YouTube video id (P1651) on the Wikidata item
   of the recording's work. One MusicBrainz request per song, one Wikidata query for all. Measured 2026-11-09 on 63
   popular recordings: MusicBrainz linked 10, Wikidata 5, together 14 (22%).
3. **The artist's official channel**, which MusicBrainz names for most artists: its uploads, read 50 to a request
   (1 quota unit each), matched by title and length. No search used.
4. **YouTube search**, within its budget (below): one search per song, its first 10 videos.

**A video is the song** when YouTube says it is public and playable in the United States, its title holds the
song's name, its title has none of `live`, `cover`, `karaoke`, `reaction`, `remix`, `instrumental`, `sped up`,
`slowed`, `8d`, `hour`, `loop` that the song's name lacks, and its length is within 15 s of the recording's, or up
to 90 s longer on the artist's own channel (a music video's intro and ending). Among those it prefers the artist's
channel, then its `<artist> - Topic` channel, then the length nearest the recording's. Open data's videos are
checked the same way.

## Outside services

His rule for every outside service: worst case first, a shared cache, ask again only when the data can have
changed, and a hard budget of at most half of each published limit, refusing past it.

| Service | Published limit | This app's budget | Used for |
|-|-|-|-|
| YouTube Data API, `search.list` | 100 calls a day | 50 a day | step 4 of a song's video |
| YouTube Data API, all else | 10,000 units a day | 5,000 a day | checking videos (1 unit per 50), channel uploads (1 per 50), the playlist (50, then 50 a song) |
| MusicBrainz | 1 request a second per address | one each 2 s | artist names, recordings' links |
| ListenBrainz (API and labs) | 1 call a second per client | one each 2 s, with his user token on the API | similar artists, top recordings, tag radio, metadata, popularity |
| Wikidata Query Service | 60 s of query time a minute | one query each 2 s | YouTube ids on works |

- **Worst case**: one list of 20 lines at 5 songs is 100 songs: 20 ListenBrainz similarity requests plus about 40
  more, 100 MusicBrainz requests (200 s), up to 100 searches (two days), and a playlist of 50 + 100 x 50 = 5,050
  units, which takes two days of the units budget as well. Smaller lists are done the same day. Only one list is
  worked on at a time, by one worker, so tabs and reloads add no requests.
- YouTube's day ends at midnight Pacific time. A song waiting for the budget says so and is looked up then; a
  playlist past the budget gets its remaining songs then.
- MusicBrainz answered "busy" (503) to the first request on 2026-11-09 and the next one normally: a 503 or 429
  waits what Retry-After (or ListenBrainz's X-RateLimit-Reset-In) says, 10 s when it says nothing, at most 3
  times. A step that still cannot reach a service, or gets a 5xx or 429, waits 5 minutes and tries again, 3 times
  in all; then it stops and says why. Any other refusal (a 4xx) stops it at once: trying again would not change
  the answer.
- **Kept answers**: MusicBrainz, ListenBrainz and Wikidata answers are kept 90 days, then asked again when needed;
  their data changes slowly. YouTube's are kept at most 30 days, as its policies require (Developer Policies
  III.E.4): each hour the worker checks again every video fact and playlist link older than 25 days (1 unit per
  50 videos); what cannot be checked is deleted once it is 30 days old, so a day of failures loses nothing. A
  video YouTube no longer has, or no longer plays, is marked gone. Deleting forgets a video found by channel or
  search whole (it is looked for again when a list needs it); one from open data keeps its id, which is
  MusicBrainz's or Wikidata's, and loses YouTube's title, channel and length. A playlist link deleted so says
  that the playlist is still in his YouTube library.
- **No scraping**: YouTube's policies forbid it (III.E.6), so `yt-dlp`'s search is not used.
- **User-Agent**: `yt-playlist-maker/<version> ( https://github.com/ernop/yt-playlist-maker )` to MusicBrainz,
  ListenBrainz and Wikidata, which each require a name and a contact. The public repository's address names only
  his GitHub account, which is public already (his choice left to the agent, 2026-11-09). Google is called with
  his project's key and sign-in only.

## Identity and his account

- His ListenBrainz user token (`docs/SETUP.md`), sent as `Authorization: Token` with every request to
  `api.listenbrainz.org`, as its documentation describes; labs gets none. It names his MetaBrainz account to
  MetaBrainz, and only reads.
- His own Google Cloud project for this app (`docs/SETUP.md`): an API key for reading YouTube, and a
  Desktop-app sign-in client for writing playlists (scope `youtube`, the narrowest that can make playlists).
  matthoom's project is not reused: its consent screen links a privacy page that names only Gmail
  (matthoom `docs/getters/GMAIL.md`, "Publishing").
- The app writes to his account only when he presses "Make the playlist on YouTube", and says the visibility
  first, as YouTube's policies require (III.D.1).
- `PRIVACY.md` says what the app keeps and sends, as YouTube's policies ask of every API client.
- Nothing personal is committed: `data/` and `config.local.json` are gitignored.

## The language model

The local model through matthoom's Ollama (`127.0.0.1:11434`), `gemma4:31b` unless `config.local.json` names
another; never an outside AI service. It holds the GPU through the gate (matthoom `docs/GPU.md`), alone, as
Ollama's models do, in the lane `now` (he waits for it), waits at most 120 s for the gate, and unloads the model
and lets go as soon as the list is read. A busy GPU is not a failure of the list: the page says so and tries again
every 5 minutes, or at once with "Try now". Trial on 2026-11-09: 8 lines read in 86.2 s from a cold start.

## Decisions

1. **The playlist is a real one in his account, unlisted by default** (2026-11-09): the only documented way to make
   a link anyone can play. YouTube's `watch_videos?video_ids=` link needs no account but is undocumented, so it
   could stop working any day and goes against his above-board rule.
2. **His ListenBrainz user token** (his choice, 2026-11-10). ListenBrainz's server requires one for
   `popularity/top-recordings-for-artist`, `top-release-groups-for-artist` and LB Radio ("Due to bad actors and AI
   scrapers ... you need to provide an Auth token for this endpoint", its `ensure_user_token_for_expensive_endpoint`,
   read 2026-11-09). Without a token only answers its cache already held came back: the probes of 2026-11-09 got
   200 from the cache (`x-cache-status: STALE`), and the first uncached artist got 401. The other way, rebuilding
   an artist's top songs from endpoints still open (the artist's release groups, their listens, their tracks,
   the tracks' listens: four requests for one), was declined: it routes around a gate MetaBrainz set on purpose,
   and its source says more of `/popularity` may follow. The token goes with every request to the API, so the
   app has one way of calling it.
3. **Similar artists come from labs' `similar-artists`, not `lb-radio/artist`**, which draws a different random set
   on each call (two calls on 2026-11-09 gave 10 and 9 similar artists, only 3 of them in both); the labs dataset
   is ordered by score.
4. **Search is the last step**: its 50 a day is the tightest limit in the app.
5. **A new repository, `yt-playlist-maker`** (his suggestion, 2026-11-09), **public** (his choice, 2026-11-10):
   the User-Agent's contact address is the repository, which MetaBrainz can then read.

## Architecture

- Python 3.12 or later, Flask, SQLite (`data/app.sqlite3`), plain HTML and JavaScript; one systemd user unit,
  `yt-playlist-maker.service`, and a Caddy route registered in mybrowser's `projects.json`.
- One worker thread in the server works on one list at a time, step by step (reading, choosing, videos, playlist),
  each step resumable after a restart; the page asks for the list's state every 2 s while it works.
- `yt_playlist_maker/net.py` is the only code that calls outside services: the User-Agent, the budgets, the kept
  answers and the backoff are there, so no other module can call without them.
- Code map: `reading.py` (the model, artist names), `choosing.py` (candidates and picks), `videos.py` (the steps
  and the match rule), `playlists.py` (making the playlist), `google_auth.py` (sign-in), `worker.py`, `web.py`,
  `store.py` (the database), and one module per service.
