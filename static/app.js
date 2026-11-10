'use strict';

// The page's state is its URL: ?list=<id> shows that list.

const STEPS = { reading: 'Reading the lines', choosing: 'Choosing songs', videos: 'Finding videos', ready: 'Ready' };
const SOURCES = { musicbrainz: 'MusicBrainz', wikidata: 'Wikidata', channel: "the artist's channel", search: 'YouTube search' };
const VISIBILITY = {
  unlisted: 'Unlisted: anyone with the link can play it; it is not in YouTube search; its page shows your channel name.',
  private: 'Private: only you, signed in to YouTube, can play it.',
  public: 'Public: anyone can find it in YouTube search and on your channel.',
};
const POLL_MS = 2000;
const SEP = ' \u00b7 ';

let songs = 3;
let pollTimer = null;
let lastState = null;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value === null || value === undefined) continue;
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  };
  const reply = await fetch(path, options);
  const data = await reply.json().catch(() => ({ error: `HTTP ${reply.status}` }));
  if (!reply.ok) throw new Error(data.error || `HTTP ${reply.status}`);
  return data;
}

function listId() {
  const id = new URLSearchParams(location.search).get('list');
  return id ? Number(id) : null;
}

function clock(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function day(iso) {
  const d = new Date(iso);
  return `${d.toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' })} ${clock(iso)}`;
}

function minutes(ms) {
  if (!ms) return '';
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function number(n) {
  return n.toLocaleString('en-US');
}

// The new list

function renderSongs(choices) {
  const box = document.getElementById('songs');
  box.replaceChildren(...choices.map(n => el('button', {
    role: 'radio', 'aria-checked': String(n === songs),
    onclick: () => { songs = n; renderSongs(choices); },
  }, n)));
}

async function find() {
  const error = document.getElementById('new-error');
  error.textContent = '';
  try {
    const made = await api('/api/lists', { text: document.getElementById('lines').value, songs });
    history.pushState(null, '', `/?list=${made.id}`);
    await refresh();
  } catch (exc) {
    error.textContent = exc.message;
  }
}

// One list

function statusLine(list) {
  if (list.error) {
    return el('p', { class: 'status bad' }, list.error, ' ',
      el('button', { class: 'small', onclick: () => act(`/api/lists/${list.id}/retry`) }, 'Try again'));
  }
  if (list.wait_until) {
    return el('p', { class: 'status wait' }, `Waiting: ${list.wait_reason} Tries again at ${clock(list.wait_until)}. `,
      el('button', { class: 'small', onclick: () => act(`/api/lists/${list.id}/retry`) }, 'Try now'));
  }
  const text = STEPS[list.step] + (list.progress ? `: ${list.progress}` : '');
  return el('p', { class: list.step === 'ready' ? 'status good' : 'status' }, text);
}

function readingLine(line) {
  const r = line.reading;
  if (!r) return el('p', { class: 'reading' }, line.error ? el('span', { class: 'bad' }, line.error) : 'Not read yet');
  const parts = [];
  if (r.kind === 'artist') {
    const a = r.artists[0];
    parts.push('Artist ', el('span', { class: 'value' }, a.mbid ? a.name : a.asked));
    if (a.comment) parts.push(` (${a.comment})`);
    if (a.mbid) parts.push(' ', el('a', { href: `https://musicbrainz.org/artist/${a.mbid}`, target: '_blank', rel: 'noopener' }, 'MusicBrainz'));
    if (a.note) parts.push(' ', el('span', { class: a.mbid ? 'wait' : 'bad' }, a.note));
    if (a.others && a.others.length) {
      const pick = el('select', { 'aria-label': 'Another artist of this name',
        onchange: (e) => e.target.value && act(`/api/lines/${line.id}/artist`, { mbid: e.target.value }) },
      el('option', { value: '' }, `or another of ${a.others.length + 1} named so`),
      a.others.map(o => el('option', { value: o.mbid }, o.comment ? `${o.name} (${o.comment})` : o.name)));
      parts.push(' ', pick);
    }
  } else {
    parts.push(r.kind === 'style' ? 'Style' : 'Description');
    if (r.tags.length) parts.push(`${SEP}tags `, el('span', { class: 'value' }, r.tags.join(', ')));
    if (r.artists.length) parts.push(`${SEP}artists `, el('span', { class: 'value' }, r.artists.map(a => a.mbid ? a.name : `${a.asked} (not in MusicBrainz)`).join(', ')));
  }
  if (r.years) parts.push(`${SEP}years `, el('span', { class: 'value' }, `${r.years[0]} to ${r.years[1]}`));
  parts.push(`${SEP}read by ${r.by === 'you' ? 'you' : r.by}`);
  if (line.error) parts.push(SEP, el('span', { class: 'bad' }, line.error));
  return el('p', { class: 'reading' }, parts);
}

function videoCell(pick, list) {
  const v = pick.video;
  if (v && v.state === 'found') {
    return el('td', {}, el('a', { href: `https://www.youtube.com/watch?v=${v.video_id}`, target: '_blank', rel: 'noopener' }, 'Watch on YouTube'),
      el('span', {}, ` from ${SOURCES[v.source] || v.source}`));
  }
  if (v && v.state === 'none') return el('td', { class: 'bad' }, 'No video found');
  if (v && v.state === 'gone') return el('td', { class: 'bad' }, 'YouTube no longer has it');
  if (list.wait_until && list.step === 'videos') return el('td', { class: 'wait' }, 'Waiting for the budget');
  return el('td', {}, list.step === 'ready' ? 'Not looked for' : 'Looking');
}

function songsTable(line, list) {
  if (!line.picks.length) return null;
  return el('table', { class: 'rows' },
    el('thead', {}, el('tr', {},
      el('th', { class: 'num' }, '#'), el('th', {}, 'Song'), el('th', {}, 'Artist'), el('th', { class: 'num' }, 'Year'),
      el('th', { class: 'num' }, 'Length'), el('th', {}, 'Why'), el('th', {}, 'Video'), el('th', {}, ''))),
    el('tbody', {}, line.picks.map((pick, n) => el('tr', {},
      el('td', { class: 'num' }, n + 1),
      el('td', { class: 'song clip', title: pick.title }, pick.title),
      el('td', { class: 'artist clip', title: pick.artist }, pick.artist),
      el('td', { class: 'num' }, pick.year || ''),
      el('td', { class: 'num' }, minutes(pick.length_ms)),
      el('td', { class: 'clip', title: pick.why }, pick.why),
      videoCell(pick, list),
      el('td', { class: 'actions' },
        el('button', { class: 'small', disabled: !line.pool_size, title: line.pool_size ? `${line.pool_size} candidates left` : 'No candidates left',
          onclick: () => act(`/api/picks/${pick.id}/swap`) }, 'Swap'),
        el('button', { class: 'small', onclick: () => act(`/api/picks/${pick.id}/remove`) }, 'Remove'))))));
}

function playlistPanel(list) {
  const p = list.playlist;
  const setup = lastState ? lastState.setup : {};
  const found = list.lines.flatMap(l => l.picks).filter(x => x.video && x.video.state === 'found').length;
  const box = el('div', { class: 'playlist' }, el('h3', {}, 'The playlist'));
  if (p && p.url) {
    box.append(el('p', { class: 'link' }, el('a', { href: p.url, target: '_blank', rel: 'noopener' }, p.title)),
      el('p', {}, el('button', { onclick: (e) => { navigator.clipboard.writeText(p.url); e.target.textContent = 'Link copied'; } }, 'Copy link'),
        ` ${VISIBILITY[p.visibility]}`));
  }
  if (p && p.step === 'saving') {
    box.append(el('p', { class: p.wait_until ? 'status wait' : 'status' },
      p.wait_until ? `Waiting: ${p.wait_reason} Adds the rest at ${clock(p.wait_until)}.` : `Adding songs: ${p.added} of ${found}`));
  } else if (p && p.step === 'saved') {
    box.append(el('p', { class: 'status good' }, `${p.added} songs on YouTube`));
  } else if (p && p.error) {
    box.append(el('p', { class: 'status bad' }, p.error));
  }
  if (p && p.step === 'saving') return box;
  if (!setup.signed_in) {
    box.append(el('p', {}, setup.google_client
      ? [el('a', { href: `/google/signin?back=${encodeURIComponent(location.pathname + location.search)}` }, 'Sign in to YouTube'),
        ' to make the playlist in your account.']
      : 'Making the playlist needs the Google sign-in client (Setup, below).'));
    return box;
  }
  const visibility = el('select', { 'aria-label': 'Who can play it' },
    Object.keys(VISIBILITY).map(v => el('option', { value: v, selected: p ? p.visibility === v : v === 'unlisted' }, v[0].toUpperCase() + v.slice(1))));
  const explain = el('span', {}, VISIBILITY[visibility.value]);
  visibility.addEventListener('change', () => { explain.textContent = VISIBILITY[visibility.value]; });
  const label = p && p.youtube_id ? 'Add the new songs to the playlist' : 'Make the playlist on YouTube';
  box.append(el('div', { class: 'controls' },
    el('button', { class: 'primary', disabled: !found || list.step !== 'ready',
      onclick: () => act(`/api/lists/${list.id}/playlist`, { visibility: visibility.value }) }, label),
    p && p.youtube_id ? null : visibility, p && p.youtube_id ? null : explain),
  el('p', { class: 'hint' }, `${found} songs have a video. Each costs 50 of YouTube's ${number(lastState.youtube_left.units_budget)} units a day; ${number(lastState.youtube_left.units)} are left today.`));
  return box;
}

function renderList(list) {
  const section = document.getElementById('list');
  section.hidden = false;
  section.replaceChildren(
    el('h2', {}, `List ${list.id}, made ${day(list.created)}`),
    statusLine(list),
    el('div', { class: 'controls' },
      el('button', { class: 'small', onclick: () => act(`/api/lists/${list.id}/read-again`) }, 'Read the lines again')),
    ...list.lines.flatMap(line => [el('h3', {}, line.text), readingLine(line), songsTable(line, list)].filter(Boolean)),
    playlistPanel(list));
}

// Earlier lists and setup

function renderState(state) {
  lastState = state;
  document.getElementById('max-lines').textContent = state.limits.lines;
  renderSongs(state.limits.songs);
  document.getElementById('earlier-rows').replaceChildren(...state.lists.map(item => el('tr', {},
    el('td', { class: 'num' }, el('a', { href: `/?list=${item.id}`, onclick: (e) => { e.preventDefault(); history.pushState(null, '', `/?list=${item.id}`); refresh(); } }, item.id)),
    el('td', {}, day(item.created)),
    el('td', { class: 'clip', title: item.lines }, item.lines),
    el('td', { class: item.error ? 'bad' : item.step === 'ready' ? 'good' : '' }, item.error ? 'Failed' : STEPS[item.step]),
    el('td', {}, item.url ? el('a', { href: item.url, target: '_blank', rel: 'noopener' }, 'Open on YouTube') : ''))));
  const s = state.setup;
  const left = state.youtube_left;
  const yes = (ok, good, bad) => el('td', { class: ok ? 'good' : 'bad' }, ok ? good : bad);
  document.getElementById('setup-rows').replaceChildren(
    el('tr', {}, el('td', {}, 'ListenBrainz token, to choose songs'), yes(s.listenbrainz_token, 'Set', 'Not set: listenbrainz_token in config.local.json')),
    el('tr', {}, el('td', {}, 'YouTube key, to find videos'), yes(s.youtube_key, 'Set', 'Not set: youtube_api_key in config.local.json')),
    el('tr', {}, el('td', {}, 'Google sign-in client'), yes(s.google_client, 'Set', 'Not set: google_client_id and google_client_secret in config.local.json')),
    el('tr', {}, el('td', {}, 'Signed in to YouTube, to make playlists'), yes(s.signed_in, 'Yes', 'No')),
    el('tr', {}, el('td', {}, 'Language model'), el('td', {}, s.language_model)),
    el('tr', {}, el('td', {}, 'YouTube searches left today'), el('td', {}, `${number(left.searches)} of ${number(left.searches_budget)}`)),
    el('tr', {}, el('td', {}, 'YouTube units left today'), el('td', {}, `${number(left.units)} of ${number(left.units_budget)}, new at ${clock(left.new_at)}`)));
  const account = document.getElementById('account');
  account.replaceChildren();
  if (s.signed_in) {
    account.append(
      el('button', { onclick: () => act('/api/google/signout', { forget: false }) }, 'Sign out of YouTube'),
      el('button', { onclick: () => confirm('Sign out, and delete what YouTube told this app: videos found by search or channel, and playlist links?') && act('/api/google/signout', { forget: true }) }, 'Sign out and delete YouTube data'));
  } else {
    if (s.google_client) {
      account.append(el('a', { href: `/google/signin?back=${encodeURIComponent(location.pathname + location.search)}` }, 'Sign in to YouTube'));
    }
    account.append(el('button', { onclick: () => confirm('Delete what YouTube told this app: videos found by search or channel, and playlist links?') && act('/api/google/signout', { forget: true }) }, 'Delete YouTube data'));
  }
}

async function act(path, body = {}) {
  try {
    await api(path, body);
  } catch (exc) {
    alert(exc.message);
  }
  await refresh();
}

async function refresh() {
  clearTimeout(pollTimer);
  const state = await api('/api/state');
  renderState(state);
  const id = listId();
  if (!id) {
    document.getElementById('list').hidden = true;
    return;
  }
  const list = await api(`/api/lists/${id}`);
  renderList(list);
  const working = (!list.error && list.step !== 'ready') || (list.playlist && list.playlist.step === 'saving');
  if (working) pollTimer = setTimeout(refresh, POLL_MS);
}

document.getElementById('find').addEventListener('click', find);
window.addEventListener('popstate', refresh);
refresh();
