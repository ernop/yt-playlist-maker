# Setup: the ListenBrainz token, the YouTube key and the sign-in client

Three things go into `config.local.json`, which is gitignored; none needs a payment method.

| Setting | For | Without it |
|-|-|-|
| `listenbrainz_token` | choosing songs: ListenBrainz's popularity endpoints require a user token (SPEC.md, decision 2) | a list stops at "Choosing songs" and says so |
| `youtube_api_key` | finding videos: check them, read an artist's channel, search | a list stops at "Finding videos" and says so |
| `google_client_id`, `google_client_secret` | making the playlist in his YouTube account | no "Sign in to YouTube" |

Restart the app after changing the file: `systemctl --user restart yt-playlist-maker`.

## The ListenBrainz token

1. Sign in at https://listenbrainz.org/login/ with his MetaBrainz account (the MusicBrainz account; making one
   needs a user name and an email address).
2. On https://listenbrainz.org/settings/, copy the **User token** into `config.local.json` as
   `listenbrainz_token`.

The token identifies his account to MetaBrainz on every request the app makes to ListenBrainz's API; it reads
public data only and never writes to his account. "Reset token" on the same page ends the old one.

## Google: the YouTube key and the sign-in client

The app needs two things from a Google Cloud project of its own (SPEC.md, "Identity and his account"):

- an **API key**, to read YouTube. Every request counts against the project's quota, 10,000 units a day; the app
  uses at most half (SPEC.md, "Outside services").
- a **Desktop app sign-in client**, to write: make the playlist in his YouTube account. Only he signs in, on this
  computer; the app keeps the sign-in in `data/google_token.json` (readable by him only).

### Steps

1. **Project.** At https://console.cloud.google.com/projectcreate make a project named `yt-playlist-maker`, and
   check that the console's project picker shows it for the steps below.
2. **The API.** At https://console.cloud.google.com/apis/library/youtube.googleapis.com press Enable.
3. **The key.** At https://console.cloud.google.com/apis/credentials: Create credentials, API key. Edit it:
   under API restrictions, "Restrict key", and pick only YouTube Data API v3; save. Copy the key into
   `config.local.json` as `youtube_api_key`.
4. **The consent screen.** At https://console.cloud.google.com/auth/overview press Get started:
   - App name `yt-playlist-maker`; the support email is his (only test users see this screen).
   - Audience: **External**. It stays in **Testing**: only the test users below can sign in, Google does not
     review the app, and a sign-in lasts 7 days, after which the page asks him to sign in again.
   - Contact email: his. Agree to the policy, Create.
5. **Test user.** Under Audience, Test users, add the Google account whose YouTube channel gets the playlists.
6. **The scope.** Under Data Access, Add or remove scopes, add `https://www.googleapis.com/auth/youtube` ("See,
   edit, and permanently delete your YouTube videos, ratings, comments and captions"), and save. It is the
   narrowest scope that can make a playlist; the app only makes playlists and adds songs to them.
7. **The client.** Under Clients, Create client, application type **Desktop app**, name `yt-playlist-maker`.
   Copy the client ID and secret into `config.local.json` as `google_client_id` and `google_client_secret`. A
   Desktop app client needs no redirect address registered: Google accepts any loopback address, and the app
   uses `http://127.0.0.1:8020/google/callback`. Google treats a desktop app's secret as one it cannot keep;
   the sign-in is protected by PKCE instead.
8. **Restart** the app, open the page, and in Setup press "Sign in to YouTube". Google says the app is not
   verified (it is in Testing); continue, and allow it.

### Afterwards

- **Sign out**: the page's "Sign out of YouTube" revokes the sign-in at Google and deletes it here; "Sign out and
  delete YouTube data" also deletes what YouTube told the app (PRIVACY.md). Google's own page for it:
  https://security.google.com/settings/security/permissions.
- **Another account**: each sign-in is one Google account; signing out and in again switches it.
