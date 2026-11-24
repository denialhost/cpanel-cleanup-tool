import {categories, formatBytes, formatPercent, totals, candidateRows} from './model.js';
import {createActivity, renderDetails, resultSummary} from './activity.js';

const $ = id => document.getElementById(id);
const state = {
  accounts: [], filter: '', user: null, items: [], selected: new Set(), quota: {},
  category: 'all', page: 0, pageSize: 9, busy: false, analysisToken: null, scanJob: null, scanStop: false, scannedAt: 0
};
const activity = createActivity();
const icons = {trash: '♧', logs: '≡', backups: '▤', spam: '✉', mail_trash: '✉'};

function node(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text != null) el.textContent = text;
  return el;
}
function message(text, error = false) {
  $('notice').hidden = false;
  $('notice').textContent = text;
  $('notice').classList.toggle('error', error);
}
function warnings(messages = [], failures = []) { renderDetails($('warnings'), messages, failures); }
function listMessage(text, error = false) {
  $('list-notice').hidden = false;
  $('list-notice').textContent = text;
  $('list-notice').classList.toggle('error', error);
}
function spamItems() { return state.items.filter(item => item.selectable && item.category === 'spam'); }

function apiUrl(action, user = state.user) {
  const url = new URL(window.CCT_API || location.pathname, location.origin);
  url.search = '';
  url.searchParams.set('action', action);
  if (user) url.searchParams.set('user', user);
  return url;
}

async function request(action, body, user = state.user) {
  const url = apiUrl(action, user);
  const response = await fetch(url, {
    method: body ? 'POST' : 'GET',
    credentials: 'same-origin',
    cache: 'no-store',
    headers: body ? {'Content-Type': 'application/json', 'X-CCT-Request': '1'} : {},
    body: body ? JSON.stringify(body) : undefined
  });
  let data;
  try { data = await response.json(); }
  catch { throw new Error('La sesión de WHM pudo haber expirado.'); }
  if (!response.ok || data.error) throw new Error(data.error || 'No se pudo completar la solicitud.');
  return data;
}

async function batchRequest(action, body) {
  for (let attempt = 0; ; attempt++) {
    try { return await request(action, body); }
    catch (err) {
      if (attempt >= 5 || !err.message.includes('ya está procesando otro lote')) throw err;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }
}

function cacheNotice(refresh) {
  if (!refresh) return;
  $('cache-notice').hidden = false;
  $('cache-message').textContent = refresh.message;
  $('cache-retry').textContent = refresh.status === 'built'
    ? 'Cuota y caché actualizadas para esta cuenta.'
    : 'Si el uso aún no cambia, pulsa «Refrescar uso en cPanel».';
}

async function followCacheRefresh(initial) {
  let refresh = initial;
  for (let attempt = 0; attempt < 8 && refresh && ['building', 'cleared', 'pending'].includes(refresh.status); attempt++) {
    cacheNotice(refresh);
    await new Promise(resolve => setTimeout(resolve, Math.max(1000, (refresh.retry_after_seconds || 3) * 1000)));
    const data = await request('refresh', {poll: true});
    refresh = data.cache_refresh;
    state.quota = data.quota;
    renderQuota();
  }
  cacheNotice(refresh);
  return refresh;
}

function visibleAccounts() {
  const query = state.filter.toLocaleLowerCase('es');
  return state.accounts.filter(row => !query || `${row.user} ${row.domain} ${row.plan}`.toLocaleLowerCase('es').includes(query));
}

function renderAccountList() {
  const rows = visibleAccounts();
  $('account-list').replaceChildren();
  $('account-empty').hidden = rows.length > 0;
  for (const row of rows) {
    const tr = node('tr');
    tr.append(
      node('td', '', row.user),
      node('td', '', row.domain || '—'),
      node('td', '', row.plan || '—'),
      node('td', '', `${row.diskused || '—'} / ${row.disklimit || '—'}`)
    );
    const action = node('td');
    const button = node('button', 'button secondary', 'Revisar');
    button.type = 'button';
    button.addEventListener('click', () => openAccount(row.user));
    action.append(button);
    tr.append(action);
    $('account-list').append(tr);
  }
}

async function loadAccounts() {
  if (state.busy) return;
  state.busy = true;
  $('reload-accounts').disabled = true;
  try {
    const data = await request('accounts', null, null);
    state.accounts = data.accounts || [];
    $('list-notice').hidden = true;
    renderAccountList();
  } catch (err) {
    listMessage(err.message, true);
  } finally {
    state.busy = false;
    $('reload-accounts').disabled = false;
  }
}

function openAccount(user) {
  state.user = user;
  state.items = [];
  state.selected.clear();
  state.quota = {};
  state.analysisToken = null;
  state.scanJob = null;
  state.page = 0;
  $('list-view').hidden = true;
  $('account-view').hidden = false;
  $('current-user').textContent = user;
  $('banner-user').textContent = user;
  $('account-title').textContent = user;
  $('analysis-account').textContent = 'Sin análisis todavía.';
  $('notice').hidden = true;
  $('warnings').hidden = true;
  $('cache-notice').hidden = true;
  renderQuota();
  renderTabs();
  renderTable();
  renderSelection();
}

function backToList() {
  state.user = null;
  $('account-view').hidden = true;
  $('list-view').hidden = false;
}

function filters() {
  return {category: state.category, search: $('search').value, age: Number($('age').value), sort: $('sort').value};
}
function visible() { return candidateRows(state.items, filters()); }

function renderQuota() {
  const q = state.quota || {};
  $('quota-limit').textContent = formatBytes(q.limit_bytes);
  $('quota-used').textContent = formatBytes(q.used_bytes);
  $('quota-free').textContent = formatBytes(q.free_bytes);
  $('quota-used-percent').textContent = q.limit_bytes ? formatPercent((q.used_bytes || 0) / q.limit_bytes * 100) : '';
}

function renderTabs() {
  $('categories').replaceChildren();
  const counts = {all: state.items.filter(i => i.selectable).length};
  for (const key of Object.keys(categories)) {
    if (key === 'all') continue;
    counts[key] = state.items.filter(i => i.selectable && i.category === key).length;
  }
  for (const [key, label] of Object.entries(categories)) {
    const button = node('button', 'category-tab');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(state.category === key));
    button.append(label, node('span', '', String(counts[key] || 0)));
    button.addEventListener('click', () => { state.category = key; state.page = 0; renderTabs(); renderTable(); renderSelection(); });
    $('categories').append(button);
  }
}

function renderTable() {
  const rows = visible();
  const start = state.page * state.pageSize;
  const page = rows.slice(start, start + state.pageSize);
  $('file-list').replaceChildren();
  $('empty').hidden = page.length > 0;
  $('visible-count').textContent = `${rows.length} candidatos visibles`;
  $('page-info').textContent = rows.length ? `${start + 1}–${Math.min(start + page.length, rows.length)} de ${rows.length}` : '—';
  $('previous-page').disabled = state.page <= 0;
  $('next-page').disabled = start + state.pageSize >= rows.length;
  for (const item of page) {
    const tr = node('tr');
    if (item.members.every(member => state.selected.has(member.id))) tr.classList.add('is-selected');
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.checked = item.members.every(member => state.selected.has(member.id));
    check.addEventListener('change', () => {
      for (const member of item.members) check.checked ? state.selected.add(member.id) : state.selected.delete(member.id);
      renderTable();
      renderSelection();
    });
    const checkCell = node('td', 'check-cell');
    checkCell.append(check);
    const name = node('div', 'file-name');
    name.append(node('span', `file-symbol ${item.category}`, icons[item.category] || '•'));
    const info = node('div', 'file-info');
    info.append(node('strong', '', item.grouped ? item.name : item.name), node('code', '', item.path || item.relative_path || ''));
    name.append(info);
    const nameCell = node('td');
    nameCell.append(name);
    const modified = node('td', 'modified', new Date((item.modified_at || 0) * 1000).toLocaleString('es-CL'));
    const size = node('td', 'size-cell', formatBytes(item.reclaimable_bytes));
    const badgeCell = node('td');
    badgeCell.append(node('span', `badge ${item.risk || 'candidate'}`, item.risk === 'review' ? 'Revisar' : 'Candidato'));
    tr.append(checkCell, nameCell, modified, size, badgeCell);
    $('file-list').append(tr);
  }
}

function renderSelection() {
  const t = totals(state.items, state.selected, state.quota);
  $('selected-size').textContent = formatBytes(t.bytes);
  $('plan-bytes').textContent = formatBytes(t.bytes);
  $('selected-count').textContent = t.picked.length ? `${t.picked.length} elementos seleccionados` : 'Ningún elemento seleccionado';
  $('review').disabled = state.busy || !t.picked.length;
  $('clear-selection').disabled = state.busy || !t.picked.length;
  $('select-spam').disabled = state.busy || !spamItems().length;
  $('select-all').checked = visible().length > 0 && visible().every(row => row.members.every(m => state.selected.has(m.id)));
}

function busy(value) {
  state.busy = value;
  $('scan').disabled = value;
  $('show-audit').disabled = value;
  $('refresh-quota').disabled = value;
  $('back-list').disabled = value;
  renderSelection();
}

async function scan() {
  if (!state.user || state.busy) return;
  const ticket = activity.begin({title: 'Analizando cuenta', message: `Revisando ${state.user}…`});
  state.scanStop = false;
  busy(true);
  $('activity-stop').hidden = false;
  $('activity-stop').disabled = false;
  try {
    if (!state.scanJob) {
      const data = await batchRequest('scan_start', {});
      state.scanJob = data.scan;
      state.items = [];
      state.selected.clear();
      state.quota = data.quota;
      state.analysisToken = null;
      renderQuota();
    }
    while (!state.scanJob.done && !state.scanStop) {
      const p = state.scanJob;
      activity.update(ticket, {
        title: p.phase === 'files' ? 'Revisando archivos' : 'Revisando correo',
        message: `${p.visited.toLocaleString('es-CL')} entradas · cuenta ${state.user}`
      });
      const data = await batchRequest('scan_step', {token: p.token});
      state.scanJob = data.scan;
    }
    if (state.scanStop) {
      message('Análisis pausado. Pulsa Analizar cuenta para continuar.');
      return;
    }
    let offset = 0;
    const items = [];
    do {
      const data = await batchRequest('scan_results', {token: state.scanJob.token, offset});
      items.push(...data.items);
      if (data.quota) state.quota = data.quota;
      offset = data.next_offset;
    } while (offset !== null);
    const p = state.scanJob;
    state.items = items;
    state.analysisToken = p.token;
    state.scannedAt = p.created;
    state.scanJob = null;
    state.page = 0;
    $('analysis-account').textContent = `${p.partial ? 'Análisis con omisiones' : 'Análisis completo'} · ${new Date(p.created * 1000).toLocaleString('es-CL')}`;
    warnings(p.warnings || []);
    renderQuota();
    renderTabs();
    renderTable();
    renderSelection();
  } catch (err) {
    if (/venció|ya no está disponible/.test(err.message)) state.scanJob = null;
    message(err.message, true);
  } finally {
    $('activity-stop').hidden = true;
    $('scan').textContent = state.scanJob ? 'Continuar análisis' : 'Analizar cuenta';
    busy(false);
    activity.end(ticket);
  }
}

function review() {
  const t = totals(state.items, state.selected, state.quota);
  if (!t.picked.length) return;
  $('confirm-summary').textContent = `Se eliminarán ${t.picked.length} elementos de ${state.user}. Ahorro estimado: ${formatBytes(t.bytes)}.`;
  $('confirm-list').replaceChildren();
  for (const item of candidateRows(t.picked)) {
    const row = node('div', 'confirm-item');
    row.append(node('span', '', item.grouped ? item.name : item.path || item.relative_path), node('strong', '', formatBytes(item.reclaimable_bytes)));
    $('confirm-list').append(row);
  }
  $('backup-ack-row').hidden = !t.picked.some(i => i.category === 'backups');
  $('backup-ack').checked = false;
  $('confirm-word').value = '';
  $('confirm-delete').disabled = true;
  $('confirmation').showModal();
}

function validateConfirm() {
  $('confirm-delete').disabled = state.busy || $('confirm-word').value !== 'ELIMINAR' || (!$('backup-ack-row').hidden && !$('backup-ack').checked);
}

async function performDelete(event) {
  event.preventDefault();
  if ($('confirm-delete').disabled) return;
  const items = totals(state.items, state.selected, state.quota).picked;
  const backupAck = $('backup-ack').checked;
  $('confirmation').close();
  busy(true);
  const ticket = activity.begin({title: 'Eliminando', message: `Cuenta ${state.user}`, completed: 0, total: items.length});
  let deleted = 0, estimated = 0, failures = [], lastRefresh = null;
  const notices = [];
  try {
    for (let offset = 0; offset < items.length; offset += 250) {
      const result = await request('delete', {
        items: items.slice(offset, offset + 250),
        confirm: 'ELIMINAR',
        backups_ack: backupAck,
        analysis_token: state.analysisToken
      });
      const ids = new Set(result.deleted_ids);
      deleted += ids.size;
      estimated += result.estimated_removed_bytes;
      failures.push(...result.failed);
      state.items = state.items.filter(i => !ids.has(i.id));
      for (const id of ids) state.selected.delete(id);
      for (const failure of result.failed) state.selected.delete(failure.id);
      state.quota = result.quota;
      notices.push(...(result.warnings || []));
      lastRefresh = result.cache_refresh;
      cacheNotice(lastRefresh);
      renderTabs();
      renderTable();
      renderSelection();
      renderQuota();
      activity.update(ticket, {completed: Math.min(offset + 250, items.length), message: `${deleted} eliminados · ${failures.length} pendientes`});
    }
    if (lastRefresh) await followCacheRefresh(lastRefresh);
    message(resultSummary({deleted, bytes: estimated, failed: failures.length}), !!failures.length);
    warnings(notices, failures);
  } catch (err) {
    message(resultSummary({deleted, interrupted: true}), true);
    warnings([...notices, err.message], failures);
  } finally {
    busy(false);
    activity.end(ticket);
  }
}

async function fetchAudit(month) {
  const url = apiUrl('audit_read', state.user);
  if (month) url.searchParams.set('month', month);
  const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store'});
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || 'No se pudo leer la auditoría.');
  return data;
}

async function openAudit() {
  if (!state.user) return;
  try {
    const monthsData = await request('audit_months');
    const months = monthsData.months || [];
    const select = $('audit-month');
    select.replaceChildren();
    const fallback = new Date().toISOString().slice(0, 7);
    for (const month of (months.length ? months : [fallback])) {
      const option = document.createElement('option');
      option.value = month;
      option.textContent = month;
      select.append(option);
    }
    await renderAudit(select.value);
    $('audit-dialog').showModal();
  } catch (err) {
    message(err.message, true);
  }
}

async function renderAudit(month) {
  const data = await fetchAudit(month);
  $('audit-list').replaceChildren();
  $('audit-empty').hidden = data.events.length > 0;
  for (const event of data.events.slice().reverse()) {
    const row = node('div', 'confirm-item');
    const when = new Date((event.ts || 0) * 1000).toLocaleString('es-CL');
    row.append(
      node('span', '', `${when} · ${event.actor || '?'} · ${event.result} · ${event.relative_path || event.id}`),
      node('strong', '', formatBytes(event.bytes || 0))
    );
    $('audit-list').append(row);
  }
}

$('reload-accounts').addEventListener('click', loadAccounts);
$('account-search').addEventListener('input', () => { state.filter = $('account-search').value; renderAccountList(); });
$('back-list').addEventListener('click', backToList);
$('scan').addEventListener('click', scan);
$('activity-stop').addEventListener('click', () => { state.scanStop = true; $('activity-stop').disabled = true; });
$('show-audit').addEventListener('click', openAudit);
$('audit-month').addEventListener('change', () => renderAudit($('audit-month').value));
$('refresh-quota').addEventListener('click', async () => {
  if (!state.user || state.busy) return;
  busy(true);
  try {
    const data = await request('refresh', {});
    state.quota = data.quota;
    await followCacheRefresh(data.cache_refresh);
    message('Cuota actualizada.');
  } catch (err) { message(err.message, true); }
  finally { busy(false); }
});
$('select-all').addEventListener('change', () => {
  for (const item of visible().flatMap(row => row.members)) {
    $('select-all').checked ? state.selected.add(item.id) : state.selected.delete(item.id);
  }
  renderTable();
  renderSelection();
});
$('select-spam').addEventListener('click', () => {
  for (const item of spamItems()) state.selected.add(item.id);
  state.category = 'spam';
  state.page = 0;
  renderTabs();
  renderTable();
  renderSelection();
});
$('clear-selection').addEventListener('click', () => { state.selected.clear(); renderTable(); renderSelection(); });
for (const id of ['search', 'age', 'sort']) $(id).addEventListener(id === 'search' ? 'input' : 'change', () => { state.page = 0; renderTable(); renderSelection(); });
$('previous-page').addEventListener('click', () => { state.page--; renderTable(); });
$('next-page').addEventListener('click', () => { state.page++; renderTable(); });
$('review').addEventListener('click', review);
for (const button of document.querySelectorAll('.close-dialog')) button.addEventListener('click', () => $('confirmation').close());
$('confirm-word').addEventListener('input', validateConfirm);
$('backup-ack').addEventListener('change', validateConfirm);
$('confirm-form').addEventListener('submit', performDelete);

loadAccounts();
