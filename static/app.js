'use strict';

// The page's state is its URL: ?list=<id> shows that list, ?mode=direct opens From a song list.

const STEPS = { reading: 'Reading the lines', choosing: 'Choosing songs', videos: 'Finding videos', ready: 'Ready' };
const DIRECT_STEPS = { videos: 'Checking imported videos', ready: 'Ready' };
const SOURCES = {
  musicbrainz: 'MusicBrainz',
  wikidata: 'Wikidata',
  channel: "the artist's channel",
  search: 'YouTube search',
  import: 'Imported link',
  sync: 'Synced library',
};
const VISIBILITY = {
  unlisted: 'Unlisted: anyone with the link can play it; it is not in YouTube search; its page shows your channel name.',
  private: 'Private: only you, signed in to YouTube, can play it.',
  public: 'Public: anyone can find it in YouTube search and on your channel.',
};
const POLL_MS = 2000;
const SEP = ' \u00b7 ';
const DIRECT_PAGE_LIMIT = 100;

let songs = 3;
let pollTimer = null;
let lastState = null;
let syncSongsCache = null;
let syncSongsVisible = false;
let syncFilterText = '';
let syncShowAll = false;
let directShowAll = false;
let syncingNow = false;

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

function currentMode() {
  return new URLSearchParams(location.search).get('mode') === 'direct' ? 'direct' : 'similar';
}

function setMode(mode) {
  const params = new URLSearchParams(location.search);
  if (mode === 'direct') params.set('mode', 'direct');
  else params.delete('mode');
  const qs = params.toString();
  history.replaceState(null, '', qs ? `/?${qs}` : '/');
  applyMode(mode);
}

function applyMode(mode) {
  const isDirect = mode === 'direct';
  document.getElementById('mode-similar').setAttribute('aria-checked', String(!isDirect));
  document.getElementById('mode-direct').setAttribute('aria-checked', String(isDirect));
  document.getElementById('similar-form').hidden = isDirect;
  document.getElementById('direct-form').hidden = !isDirect;
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
  return Number(n || 0).toLocaleString('en-US');
}

// New list (similar songs or direct song list)

function renderSongs(choices) {
  const box = document.getElementById('songs');
  box.replaceChildren(...choices.map(n => el('button', {
    type: 'button',
    role: 'radio',
    'aria-checked': String(n === songs),
    onclick: () => { songs = n; renderSongs(choices); },
  }, n)));
}

async function find() {
  const error = document.getElementById('new-error');
  error.textContent = '';
  try {
    const made = await api('/api/lists', { mode: 'similar', text: document.getElementById('lines').value, songs });
    directShowAll = false;
    history.pushState(null, '', `/?list=${made.id}`);
    await refresh();
  } catch (exc) {
    error.textContent = exc.message;
  }
}

async function importDirect() {
  const error = document.getElementById('direct-error');
  error.textContent = '';
  try {
    const made = await api('/api/lists', {
      mode: 'direct',
      text: document.getElementById('direct-text').value,
      title: document.getElementById('direct-title').value,
      target_playlist: document.getElementById('direct-target').value,
    });
    directShowAll = false;
    history.pushState(null, '', `/?list=${made.id}&mode=direct`);
    await refresh();
  } catch (exc) {
    error.textContent = exc.message;
  }
}

function handleDirectFile(file) {
  if (!file) return;
  const error = document.getElementById('direct-error');
  const label = document.getElementById('direct-file-name');
  error.textContent = '';
  label.textContent = file.name;
  const reader = new FileReader();
  reader.onload = () => {
    const text = String(reader.result || '');
    document.getElementById('direct-text').value = text;
    const titleInput = document.getElementById('direct-title');
    if (!titleInput.value.trim() && text.trim().startsWith('{')) {
      try {
        const parsed = JSON.parse(text);
        if (parsed && typeof parsed.title === 'string' && parsed.title.trim()) {
          titleInput.value = parsed.title.trim();
        }
      } catch (_ignored) {
        // Invalid JSON will be reported on Import songs.
      }
    }
  };
  reader.onerror = () => {
    error.textContent = `Could not read ${file.name}`;
  };
  reader.readAsText(file);
}

// One list

function statusLine(list) {
  if (list.error) {
    return el('p', { class: 'status bad' }, list.error, ' ',
      el('button', { type: 'button', class: 'small', onclick: () => act(`/api/lists/${list.id}/retry`) }, 'Try again'));
  }
  if (list.wait_until) {
    return el('p', { class: 'status wait' }, `Waiting: ${list.wait_reason} Tries again at ${clock(list.wait_until)}. `,
      el('button', { type: 'button', class: 'small', onclick: () => act(`/api/lists/${list.id}/retry`) }, 'Try now'));
  }
  const labels = list.mode === 'direct' ? DIRECT_STEPS : STEPS;
  const text = (labels[list.step] || STEPS[list.step] || list.step) + (list.progress ? `: ${list.progress}` : '');
  return el('p', { class: list.step === 'ready' ? 'status good' : 'status' }, text);
}

function readingLine(line, list) {
  if (list.mode === 'direct') {
    const total = line.picks.length;
    const found = line.picks.filter(x => x.video && x.video.state === 'found').length;
    const gone = line.picks.filter(x => x.video && (x.video.state === 'gone' || x.video.state === 'none')).length;
    const format = line.reading && line.reading.format ? line.reading.format : 'direct';
    const parts = [
      'Imported ', el('span', { class: 'value' }, number(total)), ' songs',
      SEP, 'ready ', el('span', { class: 'value good' }, number(found)),
    ];
    if (gone) parts.push(SEP, 'unavailable ', el('span', { class: 'value bad' }, number(gone)));
    parts.push(SEP, `format ${format}`);
    if (list.target_playlist_id) parts.push(SEP, `target playlist ${list.target_playlist_id}`);
    return el('p', { class: 'reading' }, parts);
  }
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
    const suffix = v.source === 'import' && !v.yt_checked ? ' (unchecked)' : ` from ${SOURCES[v.source] || v.source}`;
    return el('td', {}, el('a', { href: `https://www.youtube.com/watch?v=${v.video_id}`, target: '_blank', rel: 'noopener' }, 'Watch on YouTube'),
      el('span', {}, suffix));
  }
  if (v && v.state === 'none') return el('td', { class: 'bad' }, 'No video found');
  if (v && v.state === 'gone') return el('td', { class: 'bad' }, 'YouTube no longer has it');
  if (list.wait_until && list.step === 'videos') return el('td', { class: 'wait' }, 'Waiting for the budget');
  return el('td', {}, list.step === 'ready' ? 'Not looked for' : 'Looking');
}

function songsTable(line, list) {
  if (!line.picks.length) return null;
  const isDirect = list.mode === 'direct';
  const shown = (isDirect && !directShowAll && line.picks.length > DIRECT_PAGE_LIMIT)
    ? line.picks.slice(0, DIRECT_PAGE_LIMIT)
    : line.picks;
  const table = el('table', { class: 'rows' },
    el('thead', {}, el('tr', {},
      el('th', { class: 'num' }, '#'), el('th', {}, 'Song'), el('th', {}, 'Artist'), el('th', { class: 'num' }, 'Year'),
      el('th', { class: 'num' }, 'Length'), el('th', {}, 'Why'), el('th', {}, 'Video'), el('th', {}, ''))),
    el('tbody', {}, shown.map((pick, n) => el('tr', {},
      el('td', { class: 'num' }, n + 1),
      el('td', { class: 'song clip', title: pick.title }, pick.title),
      el('td', { class: 'artist clip', title: pick.artist }, pick.artist),
      el('td', { class: 'num' }, pick.year || ''),
      el('td', { class: 'num' }, minutes(pick.length_ms)),
      el('td', { class: 'clip', title: pick.why }, pick.why),
      videoCell(pick, list),
      el('td', { class: 'actions' },
        isDirect ? null : el('button', {
          type: 'button',
          class: 'small',
          disabled: !line.pool_size,
          title: line.pool_size ? `${line.pool_size} candidates left` : 'No candidates left',
          onclick: () => act(`/api/picks/${pick.id}/swap`),
        }, 'Swap'),
        el('button', { type: 'button', class: 'small', onclick: () => act(`/api/picks/${pick.id}/remove`) }, 'Remove'))))));
  if (shown.length < line.picks.length) {
    return el('div', {},
      table,
      el('div', { class: 'controls' },
        el('button', {
          type: 'button',
          class: 'small',
          onclick: () => { directShowAll = true; renderList(list); },
        }, `Show all ${number(line.picks.length)} songs (showing first ${DIRECT_PAGE_LIMIT})`)));
  }
  return table;
}

function playlistPanel(list) {
  const p = list.playlist;
  const setup = lastState ? lastState.setup : {};
  const found = list.lines.flatMap(l => l.picks).filter(x => x.video && x.video.state === 'found').length;
  const box = el('div', { class: 'playlist' }, el('h3', {}, 'The playlist'));
  if (p && p.url) {
    box.append(el('p', { class: 'link' }, el('a', { href: p.url, target: '_blank', rel: 'noopener' }, p.title)),
      el('p', {}, el('button', {
        type: 'button',
        onclick: (e) => { navigator.clipboard.writeText(p.url); e.target.textContent = 'Link copied'; },
      }, 'Copy link'), ` ${VISIBILITY[p.visibility] || p.visibility}`));
  }
  if (p && p.step === 'saving') {
    const skippedNote = p.skipped ? `, ${number(p.skipped)} unavailable skipped` : '';
    box.append(el('p', { class: p.wait_until ? 'status wait' : 'status' },
      p.wait_until
        ? `Waiting: ${p.wait_reason} Adds the rest at ${clock(p.wait_until)} (${number(p.added)} of ${number(found)} added${skippedNote}).`
        : `Adding songs: ${number(p.added)} of ${number(found)}${skippedNote}`));
  } else if (p && p.step === 'saved') {
    const skippedNote = p.skipped ? ` (${number(p.skipped)} unavailable on YouTube skipped)` : '';
    box.append(el('p', { class: 'status good' }, `${number(p.added)} songs on YouTube${skippedNote}`));
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
  const hasExistingId = Boolean((p && p.youtube_id) || list.target_playlist_id);
  const visibility = el('select', { 'aria-label': 'Who can play it' },
    Object.keys(VISIBILITY).map(v => el('option', { value: v, selected: p ? p.visibility === v : v === 'unlisted' }, v[0].toUpperCase() + v.slice(1))));
  const explain = el('span', {}, VISIBILITY[visibility.value]);
  visibility.addEventListener('change', () => { explain.textContent = VISIBILITY[visibility.value]; });
  let label = 'Make the playlist on YouTube';
  if (p && p.step === 'failed') {
    label = hasExistingId ? 'Resume adding songs to the playlist' : 'Retry making the playlist on YouTube';
  } else if (hasExistingId) {
    label = 'Add the missing songs to the playlist';
  }
  box.append(el('div', { class: 'controls' },
    el('button', {
      type: 'button',
      class: 'primary',
      disabled: !found || list.step !== 'ready',
      onclick: () => act(`/api/lists/${list.id}/playlist`, { visibility: visibility.value }),
    }, label),
    hasExistingId ? null : visibility,
    hasExistingId ? null : explain),
  el('p', { class: 'hint' },
    `${number(found)} songs have a video. Each costs 50 of YouTube's ${number(lastState.youtube_left.units_budget)} units a day; `
    + `${number(lastState.youtube_left.units)} are left today (~${Math.floor(lastState.youtube_left.units / 50)} songs today, resumes automatically after midnight Pacific).`));
  return box;
}

function renderList(list) {
  const section = document.getElementById('list');
  section.hidden = false;
  const heading = list.mode === 'direct' && list.title
    ? `List ${list.id}: ${list.title}, made ${day(list.created)}`
    : `List ${list.id}, made ${day(list.created)}`;
  const actionRow = list.mode === 'direct'
    ? el('div', { class: 'controls' },
      el('button', { type: 'button', class: 'small', onclick: () => act(`/api/lists/${list.id}/read-again`) }, 'Re-check videos'))
    : el('div', { class: 'controls' },
      el('button', { type: 'button', class: 'small', onclick: () => act(`/api/lists/${list.id}/read-again`) }, 'Read the lines again'));
  section.replaceChildren(
    el('h2', {}, heading),
    statusLine(list),
    actionRow,
    ...list.lines.flatMap(line => [
      list.mode === 'direct' ? null : el('h3', {}, line.text),
      readingLine(line, list),
      songsTable(line, list),
    ].filter(Boolean)),
    playlistPanel(list));
}

// Synced library card

async function loadSyncSongs() {
  const data = await api('/api/sync');
  syncSongsCache = data.songs || [];
  renderSync(data.status);
}

async function triggerSyncNow() {
  if (syncingNow) return;
  syncingNow = true;
  if (lastState) renderSync(lastState.sync);
  try {
    const data = await api('/api/sync/now', {});
    syncSongsCache = data.songs || [];
    await refresh();
  } catch (exc) {
    alert(exc.message);
  } finally {
    syncingNow = false;
    if (lastState) renderSync(lastState.sync);
  }
}

function syncMetric(value, label, cls = '') {
  return el('span', { class: 'metric' },
    el('span', { class: cls ? `metric-val ${cls}` : 'metric-val' }, number(value)),
    el('span', { class: 'metric-label' }, label));
}

function renderSyncSongsTable(host, countNode) {
  const q = syncFilterText.trim().toLowerCase();
  const matches = q
    ? syncSongsCache.filter(s => `${s.title} ${s.artist} ${s.year} ${s.video_id}`.toLowerCase().includes(q))
    : syncSongsCache;
  const shown = (!syncShowAll && matches.length > DIRECT_PAGE_LIMIT) ? matches.slice(0, DIRECT_PAGE_LIMIT) : matches;
  const stateLabel = { synced: ['On YouTube', 'good'], pending: ['Waiting', 'wait'], unavailable: ['Unavailable', 'bad'] };
  countNode.textContent = `${number(matches.length)} of ${number(syncSongsCache.length)} songs`;
  const nodes = [
    el('table', { class: 'rows' },
      el('thead', {}, el('tr', {},
        el('th', { class: 'num' }, '#'), el('th', {}, 'Song'), el('th', {}, 'Artist'),
        el('th', { class: 'num' }, 'Year'), el('th', {}, 'Status'), el('th', {}, 'Video'))),
      el('tbody', {}, shown.map((item, idx) => {
        const [text, cls] = stateLabel[item.state] || [item.state, ''];
        return el('tr', {},
          el('td', { class: 'num' }, idx + 1),
          el('td', { class: 'song clip', title: item.title }, item.title),
          el('td', { class: 'artist clip', title: item.artist }, item.artist),
          el('td', { class: 'num' }, item.year),
          el('td', { class: cls }, text),
          el('td', {}, el('a', { href: item.url, target: '_blank', rel: 'noopener' }, 'Watch')));
      }))),
  ];
  if (shown.length < matches.length) {
    nodes.push(el('div', { class: 'controls' },
      el('button', {
        type: 'button',
        class: 'small',
        onclick: () => { syncShowAll = true; renderSyncSongsTable(host, countNode); },
      }, `Show all ${number(matches.length)} matching songs`)));
  }
  host.replaceChildren(...nodes);
}

function renderSyncSongsBlock() {
  if (!syncSongsVisible || !syncSongsCache) return null;
  const countNode = el('span', {});
  const tableHost = el('div', {});
  renderSyncSongsTable(tableHost, countNode);
  return el('div', {},
    el('div', { class: 'controls' },
      el('input', {
        type: 'search',
        class: 'text-input filter-input',
        value: syncFilterText,
        placeholder: 'Filter synced favorites',
        oninput: (e) => {
          syncFilterText = e.target.value;
          syncShowAll = false;
          renderSyncSongsTable(tableHost, countNode);
        },
      }),
      countNode),
    tableHost);
}

function renderSync(info) {
  const host = document.getElementById('sync-body');
  if (!info || !info.configured) {
    host.replaceChildren(
      el('p', {}, info && info.partial
        ? el('span', { class: 'bad' }, 'Set both sync_url and sync_key in config.local.json to enable encrypted library sync.')
        : 'Optional automatic two-way sync between voice-wei favorites on your phone and a YouTube playlist here.'),
      el('p', { class: 'hint' },
        'Generate a sync key with ',
        el('code', {}, 'uv run python -m yt_playlist_maker.sync --init-key'),
        ', place ', el('code', {}, 'sync-auth.json'), ' on your host, and add ',
        el('code', {}, 'sync_url'), ' and ', el('code', {}, 'sync_key'),
        ' to ', el('code', {}, 'config.local.json'), ' (see ', el('code', {}, 'docs/SETUP.md'), ').'));
    return;
  }

  const nodes = [];
  nodes.push(el('div', { class: 'metrics' },
    syncMetric(info.favorites_count, 'favorites'),
    syncMetric(info.synced_count, 'on YouTube', 'good'),
    syncMetric(info.pending_count, 'waiting', info.pending_count ? 'wait' : ''),
    syncMetric(info.unavailable_count, 'unavailable', info.unavailable_count ? 'bad' : '')));

  if (info.playlist_url) {
    nodes.push(el('p', {},
      el('strong', {}, 'YouTube playlist: '),
      el('a', { href: info.playlist_url, target: '_blank', rel: 'noopener' }, info.playlist_title || info.playlist_id),
      ' ',
      el('button', {
        type: 'button',
        class: 'small',
        onclick: (e) => { navigator.clipboard.writeText(info.playlist_url); e.target.textContent = 'Link copied'; },
      }, 'Copy link')));
  } else if (lastState && !lastState.setup.signed_in) {
    nodes.push(el('p', {}, 'Sign in to YouTube in Setup below to push favorites into a YouTube playlist automatically.'));
  }

  if (info.last_error) {
    nodes.push(el('p', { class: 'status bad' }, info.last_error));
  } else if (info.wait_until) {
    nodes.push(el('p', { class: 'status wait' }, `Waiting: ${info.wait_reason} Resumes after ${clock(info.wait_until)}.`));
  }

  const times = [];
  if (info.url_host) times.push(`Host ${info.url_host}`);
  times.push(`checks every ${info.sync_minutes} min`);
  if (info.last_pull) times.push(`last pull ${day(info.last_pull)}`);
  if (info.last_push) times.push(`last push ${day(info.last_push)}`);
  if (info.pulled_from_youtube) times.push(`${number(info.pulled_from_youtube)} pulled from YouTube last run`);
  nodes.push(el('p', { class: 'hint' }, times.join(SEP)));

  nodes.push(el('div', { class: 'controls' },
    el('button', {
      type: 'button',
      class: 'primary',
      disabled: syncingNow,
      onclick: triggerSyncNow,
    }, syncingNow ? 'Syncing...' : 'Sync now'),
    info.favorites_count ? el('button', {
      type: 'button',
      onclick: async () => {
        syncSongsVisible = !syncSongsVisible;
        if (syncSongsVisible && !syncSongsCache) await loadSyncSongs();
        else renderSync(info);
      },
    }, syncSongsVisible ? 'Hide songs' : `Browse ${number(info.favorites_count)} favorites`) : null));

  const songsBlock = renderSyncSongsBlock();
  if (songsBlock) nodes.push(songsBlock);
  host.replaceChildren(...nodes);
}

// Earlier lists and setup

function renderState(state) {
  lastState = state;
  document.getElementById('max-lines').textContent = state.limits.lines;
  document.getElementById('max-direct-songs').textContent = number(state.limits.direct_songs);
  renderSongs(state.limits.songs);
  renderSync(state.sync);
  document.getElementById('earlier-rows').replaceChildren(...state.lists.map(item => el('tr', {},
    el('td', { class: 'num' }, el('a', {
      href: `/?list=${item.id}`,
      onclick: (e) => {
        e.preventDefault();
        directShowAll = false;
        history.pushState(null, '', `/?list=${item.id}`);
        refresh();
      },
    }, item.id)),
    el('td', {}, day(item.created)),
    el('td', { class: 'clip', title: item.lines }, item.lines),
    el('td', { class: item.error ? 'bad' : item.step === 'ready' ? 'good' : '' },
      item.error ? 'Failed' : ((item.mode === 'direct' ? DIRECT_STEPS[item.step] : STEPS[item.step]) || item.step)),
    el('td', {}, item.url ? el('a', { href: item.url, target: '_blank', rel: 'noopener' }, 'Open on YouTube') : ''))));
  const s = state.setup;
  const left = state.youtube_left;
  const yes = (ok, good, bad) => el('td', { class: ok ? 'good' : 'bad' }, ok ? good : bad);
  document.getElementById('setup-rows').replaceChildren(
    el('tr', {}, el('td', {}, 'ListenBrainz token, to choose songs'), yes(s.listenbrainz_token, 'Set', 'Not set: listenbrainz_token in config.local.json')),
    el('tr', {}, el('td', {}, 'YouTube key, to find videos'), yes(s.youtube_key, 'Set', 'Not set: youtube_api_key in config.local.json')),
    el('tr', {}, el('td', {}, 'Google sign-in client'), yes(s.google_client, 'Set', 'Not set: google_client_id and google_client_secret in config.local.json')),
    el('tr', {}, el('td', {}, 'Signed in to YouTube, to make playlists'), yes(s.signed_in, 'Yes', 'No')),
    el('tr', {}, el('td', {}, 'Encrypted library sync'), yes(s.sync_configured, 'Configured', 'Optional: sync_url and sync_key in config.local.json')),
    el('tr', {}, el('td', {}, 'Language model'), el('td', {}, s.language_model)),
    el('tr', {}, el('td', {}, 'YouTube searches left today'), el('td', {}, `${number(left.searches)} of ${number(left.searches_budget)}`)),
    el('tr', {}, el('td', {}, 'YouTube units left today'), el('td', {}, `${number(left.units)} of ${number(left.units_budget)}, new at ${clock(left.new_at)}`)));
  const account = document.getElementById('account');
  account.replaceChildren();
  if (s.signed_in) {
    account.append(
      el('button', { type: 'button', onclick: () => act('/api/google/signout', { forget: false }) }, 'Sign out of YouTube'),
      el('button', { type: 'button', onclick: () => confirm('Sign out, and delete what YouTube told this app: videos found by search or channel, and playlist links?') && act('/api/google/signout', { forget: true }) }, 'Sign out and delete YouTube data'));
  } else {
    if (s.google_client) {
      account.append(el('a', { href: `/google/signin?back=${encodeURIComponent(location.pathname + location.search)}` }, 'Sign in to YouTube'));
    }
    account.append(el('button', { type: 'button', onclick: () => confirm('Delete what YouTube told this app: videos found by search or channel, and playlist links?') && act('/api/google/signout', { forget: true }) }, 'Delete YouTube data'));
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
  applyMode(currentMode());
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

document.getElementById('mode-similar').addEventListener('click', () => setMode('similar'));
document.getElementById('mode-direct').addEventListener('click', () => setMode('direct'));
document.getElementById('find').addEventListener('click', find);
document.getElementById('import-btn').addEventListener('click', importDirect);
document.getElementById('direct-file-btn').addEventListener('click', () => document.getElementById('direct-file-input').click());
document.getElementById('direct-file-input').addEventListener('change', (e) => handleDirectFile(e.target.files && e.target.files[0]));
window.addEventListener('popstate', refresh);
refresh();
