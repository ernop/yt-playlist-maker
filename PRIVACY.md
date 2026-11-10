# yt-playlist-maker: privacy

yt-playlist-maker runs on one computer, for the person who runs it. It uses the **YouTube API Services** to find
songs' videos and to make playlists in the YouTube account he signs in with. By using it he agrees to be bound by
the YouTube Terms of Service (https://www.youtube.com/t/terms). Google's handling of what YouTube receives is
under the Google Privacy Policy (https://policies.google.com/privacy).

## What it keeps

Everything stays on this computer, in the app's `data/` folder, and goes nowhere else.

- His lists, as he typed them, how each line was read, and the songs chosen.
- From MusicBrainz, ListenBrainz and Wikidata: their answers, for 90 days.
- From YouTube: each song's video id, and the video's title, channel and length; the ids of the playlists it
  made. These are kept at most 30 days: before then they are checked with YouTube again, or deleted.
- His Google sign-in (an access token and a refresh token), in `data/google_token.json`, readable by his user
  only, until he signs out.

It sets no cookies, has no accounts of its own, and collects nothing about anyone he shares a playlist with.

## What it sends, and to whom

- **MusicBrainz, ListenBrainz, Wikidata** (MetaBrainz Foundation, Wikimedia Foundation): the artist names in his
  lines, and the MusicBrainz ids of artists and recordings. Each request names the app and its public code
  repository, as they require. Requests to ListenBrainz's API also carry his ListenBrainz user token, which
  names his MetaBrainz account to MetaBrainz.
- **YouTube** (Google), with the app's API key: video ids to check, artists' channel addresses, and searches of
  a song's artist and title.
- **YouTube** (Google), signed in as him, only when he presses "Make the playlist on YouTube" or "Add the new
  songs to the playlist": a new playlist with the visibility he picked (unlisted unless he picks private or
  public), its title and description, made from his lines, and the songs' videos.
- The language model that reads his lines runs on this computer; the lines are sent to no AI service.

Nothing is sold or shared with anyone else.

## Removing it

- "Sign out of YouTube" revokes the sign-in at Google and deletes it here.
- "Sign out and delete YouTube data" also deletes what YouTube told the app: the videos it found by channel and
  search, YouTube's titles, channels and lengths, and the playlists' links. The playlists stay in his YouTube
  account, which is where he deletes them.
- Google's page for every app's access to his account: https://security.google.com/settings/security/permissions.
- Deleting the `data/` folder removes everything the app keeps.
