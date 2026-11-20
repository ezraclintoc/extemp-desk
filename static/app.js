'use strict';

const state = {
  q: '',
  days: 90,
  source: '',
  openId: null,
  poll: null,
  feeds: [],
  areas: [],
};

const $ = (sel) => document.querySelector(sel);

/* ------------------------------------------------------------------ helpers */
function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function snippetHtml(raw) {
  // Escape everything, then re-enable the <mark> tags our SQL snippet added.
  return esc(raw).replace(/&lt;mark&gt;/g, '<mark>').replace(/&lt;\/mark&gt;/g, '</mark>');
}

function fmtBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function hostOf(url) {
  return String(url || '').split('//')[1]?.split('/')[0]?.replace('www.', '') || url;
}

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({ error: 'bad response from server' }));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function post(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

let toastTimer = null;
function toast(message, isError) {
  const el = $('#toast');
  el.textContent = message;
  el.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast'; }, 4200);
}

/* --------------------------------------------------------------------- tabs */
document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('is-active', t === tab));
    document.querySelectorAll('.view').forEach((v) => {
      v.classList.toggle('is-active', v.id === `view-${tab.dataset.view}`);
    });
    if (tab.dataset.view === 'search') $('#q').focus();
    if (tab.dataset.view === 'sources') loadSources();
  });
});

/* -------------------------------------------------------------------- stats */
async function loadStats() {
  try {
    const data = await api('/api/stats');
    const s = data.stats || {};
    const disk = data.disk || {};
    $('#stats').innerHTML =
      `<span><b>${s.total || 0}</b> articles</span>` +
      `<span><b>${s.last90 || 0}</b> in 90 days</span>` +
      `<span><b>${s.sources || 0}</b> sources</span>` +
      `<span><b>${fmtBytes(disk.disk_bytes)}</b> on disk</span>`;

    const detail = s.total
      ? `Library uses ${fmtBytes(disk.disk_bytes)} on disk — ${fmtBytes(disk.text_bytes)} of article text ` +
        `(≈${fmtBytes(disk.avg_article_bytes)} each) plus a ${fmtBytes(disk.index_bytes)} search index.`
      : 'Your library is empty — nothing stored yet.';
    const diskLine = $('#disk-usage');
    if (diskLine) diskLine.textContent = detail;

    const select = $('#source');
    const keep = select.value;
    select.innerHTML = '<option value="">Every source</option>' +
      (data.sources || [])
        .map((row) => `<option value="${esc(row.source)}">${esc(row.source)} (${row.n})</option>`)
        .join('');
    select.value = keep;
  } catch (err) {
    console.error(err);
  }
}

/* ------------------------------------------------------------------ sources */
async function loadSources() {
  try {
    const data = await api('/api/sources');
    state.feeds = data.feeds || [];
    state.areas = data.areas || [];
    renderAreas();
    renderSources();
    const enabled = state.feeds.filter((f) => f.enabled).length;
    const label = `${enabled} of ${state.feeds.length} sources enabled`;
    const count = $('#feed-count');
    if (count) count.textContent = label;
    const summary = $('#source-summary');
    if (summary) summary.textContent = label;
  } catch (err) {
    console.error(err);
  }
}

function renderAreas() {
  const select = $('#new-area');
  if (!select) return;
  const keep = select.value;
  select.innerHTML = state.areas
    .map((area) => `<option value="${esc(area)}">${esc(area)}</option>`)
    .join('');
  if (keep) select.value = keep;
}

function statusBadge(feed) {
  if (!feed.last_checked) return '<span class="badge">untested</span>';
  const when = String(feed.last_checked).slice(5, 10).replace('-', '/');
  if (feed.last_status === 'ok') {
    return `<span class="badge good">${feed.last_count} items · ${when}</span>`;
  }
  const why = (feed.last_error || 'failed').split(':').slice(-1)[0].trim().slice(0, 40);
  return `<span class="badge bad" title="${esc(feed.last_error || '')}">${esc(why)}</span>`;
}

function renderSources() {
  const list = $('#feed-list');
  if (!list) return;
  if (!state.feeds.length) {
    list.innerHTML = '<div class="placeholder">No sources yet.</div>';
    return;
  }
  const groups = new Map();
  for (const feed of state.feeds) {
    const key = feed.area || 'Custom';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(feed);
  }
  const parts = [];
  for (const [area, feeds] of [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    const on = feeds.filter((f) => f.enabled).length;
    parts.push(`<div class="feedgroup">
      <div class="grouph"><span>${esc(area)}</span><span class="gn">${on}/${feeds.length}</span></div>
      ${feeds.map(feedRow).join('')}
    </div>`);
  }
  list.innerHTML = parts.join('');
  list.querySelectorAll('input[type="checkbox"]').forEach((box) => {
    box.addEventListener('change', async () => {
      const id = Number(box.dataset.id);
      try {
        const data = await post('/api/sources', { action: 'toggle', id, enabled: box.checked });
        state.feeds = data.feeds;
        renderSources();
        const enabled = state.feeds.filter((f) => f.enabled).length;
        const label = `${enabled} of ${state.feeds.length} sources enabled`;
        $('#source-summary').textContent = label;
        $('#feed-count').textContent = label;
      } catch (err) {
        toast(err.message, true);
      }
    });
  });
  list.querySelectorAll('[data-act="test"]').forEach((btn) => {
    btn.addEventListener('click', () => testSources(Number(btn.dataset.id), btn));
  });
  list.querySelectorAll('[data-act="delete"]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const id = Number(btn.dataset.id);
      if (!confirm('Remove this source from your list?')) return;
      try {
        const data = await post('/api/sources', { action: 'delete', id });
        state.feeds = data.feeds;
        renderSources();
      } catch (err) {
        toast(err.message, true);
      }
    });
  });
}

function feedRow(feed) {
  return `<div class="feedrow${feed.enabled ? '' : ' is-off'}">
    <label class="feedcheck">
      <input type="checkbox" data-id="${feed.id}" ${feed.enabled ? 'checked' : ''}>
      <span class="fname">${esc(feed.name)}</span>
    </label>
    <span class="fhost">${esc(hostOf(feed.url))}</span>
    ${statusBadge(feed)}
    <button class="btn tiny" data-act="test" data-id="${feed.id}">Test</button>
    ${feed.builtin ? '' : `<button class="btn tiny danger" data-act="delete" data-id="${feed.id}">Remove</button>`}
  </div>`;
}

async function testSources(id, button) {
  const target = button || $('#test-sources');
  const original = target.textContent;
  target.disabled = true;
  target.textContent = id ? '…' : 'Testing…';
  try {
    const data = await post('/api/sources/test', id ? { id } : {});
    state.feeds = data.feeds;
    renderSources();
    const results = data.results || [];
    const ok = results.filter((r) => r.status === 'ok').length;
    if (id) {
      const result = results[0] || {};
      toast(
        result.status === 'ok'
          ? `Feed works — ${result.count} recent items`
          : `Feed failed: ${(result.error || '').slice(0, 80)}`,
        result.status !== 'ok'
      );
    } else {
      toast(`${ok} of ${results.length} feeds answered`);
    }
  } catch (err) {
    toast(err.message, true);
  } finally {
    target.disabled = false;
    target.textContent = original;
  }
}

/* ------------------------------------------------------------------- search */
let searchTimer = null;
function scheduleSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(search, 200);
}

async function search() {
  const params = new URLSearchParams({
    q: state.q,
    days: String(state.days),
    limit: '60',
  });
  if (state.source) params.set('source', state.source);
  try {
    const data = await api(`/api/search?${params}`);
    renderResults(data);
  } catch (err) {
    toast(err.message, true);
  }
}

function renderResults(data) {
  const items = data.items || [];
  const total = data.total || 0;
  $('#count').textContent = total
    ? `${total.toLocaleString()} ${total === 1 ? 'article' : 'articles'}${data.query ? '' : ' (newest first)'}`
    : '';

  if (!items.length) {
    $('#results').innerHTML = total === 0 && state.q
      ? '<div class="placeholder">Nothing matches. Try fewer words, or widen the date range.</div>'
      : '<div class="placeholder">Your library is empty. File some articles first.</div>';
    return;
  }

  $('#results').innerHTML = items.map((item) => `
    <button class="hit${item.id === state.openId ? ' is-open' : ''}" data-id="${item.id}">
      <div class="title">${esc(item.title)}</div>
      <div class="meta">
        <span>${esc(item.source || 'unknown')}</span>
        <span>${esc(item.published || 'undated')}</span>
        <span>${item.word_count}w</span>
      </div>
      ${item.snippet ? `<div class="snip">${snippetHtml(item.snippet)}</div>` : ''}
    </button>
  `).join('');

  $('#results').querySelectorAll('.hit').forEach((el) => {
    el.addEventListener('click', () => openArticle(Number(el.dataset.id)));
  });
}

async function openArticle(id) {
  try {
    const article = await api(`/api/article?id=${id}`);
    state.openId = id;
    document.querySelectorAll('.hit').forEach((el) => {
      el.classList.toggle('is-open', Number(el.dataset.id) === id);
    });
    const paragraphs = (article.text || '')
      .split(/\n{2,}/)
      .map((p) => `<p>${esc(p.trim()).replace(/\n/g, '<br>')}</p>`)
      .join('');
    $('#reader').innerHTML = `
      <h2>${esc(article.title)}</h2>
      <div class="rmeta">
        <span>${esc(article.source || 'unknown')}</span>
        <span>${esc(article.published || 'undated')}</span>
        <span>${esc((article.area || ''))}</span>
        <a href="${esc(article.url)}" target="_blank" rel="noopener">open original ↗</a>
      </div>
      ${paragraphs}
      <div class="wordcount">${article.word_count} words · filed ${esc((article.fetched_at || '').slice(0, 10))}</div>
    `;
    $('#reader').scrollTop = 0;
  } catch (err) {
    toast(err.message, true);
  }
}

/* -------------------------------------------------------------------- filing */
function setBusy(busy) {
  document.querySelectorAll('.btn, .seg button').forEach((b) => { b.disabled = busy; });
}

async function runJob(path, body, label) {
  clearLog();
  setStatus('running', `${label}…`);
  setBusy(true);
  try {
    const { job } = await post(path, body);
    trackJob(job.id, label);
  } catch (err) {
    setStatus('error', 'failed');
    setBusy(false);
    toast(err.message, true);
  }
}

function clearLog() {
  $('#job-log').innerHTML = '';
  $('#job-bar').style.width = '0%';
}

function setStatus(kind, text) {
  const el = $('#job-status');
  el.className = `status ${kind}`;
  el.textContent = text;
}

function trackJob(jobId, label) {
  clearInterval(state.poll);
  let seen = 0;
  state.poll = setInterval(async () => {
    let job;
    try {
      job = await api(`/api/job?id=${jobId}`);
    } catch (err) {
      return;
    }
    const lines = (job.log || []).slice(seen);
    seen = (job.log || []).length;
    const log = $('#job-log');
    for (const line of lines) {
      const div = document.createElement('div');
      div.innerHTML = `<span class="t">${esc(line.t)}</span>${esc(line.m)}`;
      log.appendChild(div);
    }
    log.scrollTop = log.scrollHeight;

    if (job.total) {
      $('#job-bar').style.width = `${Math.min(100, Math.round((job.done / job.total) * 100))}%`;
    }

    if (job.status !== 'running') {
      clearInterval(state.poll);
      setBusy(false);
      $('#job-bar').style.width = '100%';
      if (job.status === 'error') {
        setStatus('error', 'failed');
        toast(job.error || 'job failed', true);
      } else {
        setStatus('done', 'finished');
        const result = job.result || {};
        const bits = [`${result.new || 0} new articles`];
        if (result.citations_filed) bits.push(`${result.citations_filed} from citations`);
        if (result.short) bits.push(`${result.short} no-text pages`);
        if (result.blocked) bits.push(`${result.blocked} blocked`);
        if (result.stale) bits.push(`${result.stale} outside window`);
        if (result.failed) bits.push(`${result.failed} errors`);
        if (result.engines) {
          const ok = result.engines.filter((e) => e.status === 'ok').length;
          bits.push(`${ok}/${result.engines.length} engines answered`);
        }
        toast(`${label}: ${bits.join(' · ')}`);
      }
      loadStats();
      if ($('#view-search').classList.contains('is-active')) search();
    }
  }, 700);
}

/* ------------------------------------------------------------------ listeners */
$('#q').addEventListener('input', (e) => {
  state.q = e.target.value;
  scheduleSearch();
});

$('#days').addEventListener('click', (e) => {
  const button = e.target.closest('button');
  if (!button) return;
  state.days = Number(button.dataset.days);
  $('#days').querySelectorAll('button').forEach((b) => b.classList.toggle('is-active', b === button));
  search();
});

$('#source').addEventListener('change', (e) => {
  state.source = e.target.value;
  search();
});

$('#run-autofile').addEventListener('click', () => {
  const enabled = state.feeds.filter((f) => f.enabled).length;
  if (!enabled) {
    toast('No sources enabled — pick some in the Sources tab', true);
    return;
  }
  runJob('/api/file', {
    days: Number($('#file-days').value) || 90,
    per_feed: Number($('#file-cap').value) || 40,
  }, 'Auto-File');
});

$('#goto-sources').addEventListener('click', () => {
  document.querySelector('.tab[data-view="sources"]').click();
});

$('#run-query').addEventListener('click', () => {
  const query = $('#query').value.trim();
  if (!query) { toast('Type some keywords first', true); return; }
  const count = query.split('\n').filter((l) => l.trim()).length;
  runJob('/api/query', {
    query,
    days: Number($('#query-days').value) || 90,
    limit: Number($('#query-limit').value) || 40,
    follow_refs: $('#follow-refs').checked,
  }, count > 1 ? `Query-File (${count} queries)` : 'Query-File');
});

$('#run-extract').addEventListener('click', async () => {
  const url = $('#extract-url').value.trim();
  if (!url) { toast('Paste an article URL first', true); return; }
  const button = $('#run-extract');
  button.disabled = true;
  button.textContent = 'Extracting…';
  try {
    const res = await post('/api/extract', { url });
    $('#extract-url').value = '';
    toast(res.created ? 'Article added to your library' : 'Already in your library');
    loadStats();
    if ($('#view-search').classList.contains('is-active')) search();
    openArticle(res.id);
  } catch (err) {
    toast(err.message, true);
  } finally {
    button.disabled = false;
    button.textContent = 'Add article';
  }
});

$('#extract-url').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') $('#run-extract').click();
});

$('#add-feed').addEventListener('click', async () => {
  const url = $('#new-url').value.trim();
  if (!url.startsWith('http')) { toast('Paste a full http(s) feed URL', true); return; }
  try {
    const data = await post('/api/sources', {
      action: 'add',
      url,
      name: $('#new-name').value.trim(),
      area: $('#new-area').value || 'Custom',
    });
    state.feeds = data.feeds;
    renderSources();
    $('#new-url').value = '';
    $('#new-name').value = '';
    const enabled = state.feeds.filter((f) => f.enabled).length;
    $('#source-summary').textContent = `${enabled} of ${state.feeds.length} sources enabled`;
    toast('Source added — testing it now…');
    const added = state.feeds.find((f) => f.url === url);
    if (added) testSources(added.id);
  } catch (err) {
    toast(err.message, true);
  }
});

$('#test-sources').addEventListener('click', () => testSources(null));
$('#enable-all').addEventListener('click', () => bulkToggle('enable_all'));
$('#disable-all').addEventListener('click', () => bulkToggle('disable_all'));

async function bulkToggle(action) {
  try {
    const data = await post('/api/sources', { action });
    state.feeds = data.feeds;
    renderSources();
    const enabled = state.feeds.filter((f) => f.enabled).length;
    const label = `${enabled} of ${state.feeds.length} sources enabled`;
    $('#source-summary').textContent = label;
    $('#feed-count').textContent = label;
  } catch (err) {
    toast(err.message, true);
  }
}

document.addEventListener('keydown', (e) => {
  if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
    e.preventDefault();
    $('#q').focus();
    $('#q').select();
  }
  if (e.key === 'Escape' && document.activeElement === $('#q')) {
    $('#q').value = '';
    state.q = '';
    search();
    $('#q').blur();
  }
});

/* --------------------------------------------------------------------- start */
(async function init() {
  await Promise.all([loadStats(), loadSources()]);
  await search();
  $('#q').focus();
})();
