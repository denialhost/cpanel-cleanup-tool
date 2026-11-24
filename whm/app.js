/* CCT WHM bundle: model + activity + app. One module, no relative imports. */
const categories = {all:'Todos',trash:'Papelera',logs:'Registros',backups:'Respaldos',spam:'Spam',mail_trash:'Papelera de correo'};
const formatBytes = value => {
  if (value == null || !Number.isFinite(value)) return '—';
  if (value === 0) return '0 B';
  const n = Math.min(4, Math.max(0, Math.floor(Math.log(Math.abs(value)) / Math.log(1024))));
  return `${new Intl.NumberFormat('es-CL',{maximumFractionDigits:n ? 2 : 0}).format(value / 1024 ** n)} ${['B','KiB','MiB','GiB','TiB'][n]}`;
};
const percent = (value, total) => total > 0 && value != null ? value / total * 100 : null;
const formatPercent = value => value == null ? '—' : `${new Intl.NumberFormat('es-CL',{maximumFractionDigits:1}).format(value)} %`;
const storageCategories = {
  mail:{label:'Correo',color:'#4f82c9'}, web:{label:'Web',color:'#9274c7'},
  databases:{label:'Bases de datos',color:'#d6a044'}, backups:{label:'Respaldos',color:'#6f9c69'},
  logs:{label:'Registros',color:'#c77e66'}, other:{label:'Otros',color:'#8a969a'}
};
function storageSegments(quota, storage) {
  const valid=n=>Number.isFinite(n)&&n>=0;
  let known=storage?.status==='ok' && storage.basis==='allocated_bytes' && Array.isArray(storage.categories) && storage.categories.length>0
    && storage.categories.every(item=>storageCategories[item.key] && valid(item.bytes))
    && new Set(storage.categories.map(item=>item.key)).size===storage.categories.length;
  let rows=known ? storage.categories.map(item=>({...item,...storageCategories[item.key]})) : [];
  const used=valid(quota?.used_bytes)?quota.used_bytes:null;
  let measured=rows.reduce((sum,item)=>sum+item.bytes,0),inconsistent=false;
  if(known && used!==null && measured>used){
    const excess=measured-used,other=rows.find(item=>item.key==='other');
    if(excess<=Math.max(1048576,used*.01) && other?.bytes>=excess){other.bytes-=excess;measured=used;}
    else {known=false;inconsistent=true;}
  }
  if(!known){rows=used===null?[]:[{key:'unknown',label:'Ocupado',color:'#8a969a',bytes:used}];measured=used||0;}
  const total=used===null?measured:used;
  if(known && total>measured) {
    const other=rows.find(item=>item.key==='other');
    if(other) other.bytes+=total-measured;
    else rows.push({key:'other',...storageCategories.other,bytes:total-measured});
  }
  const limit=valid(quota?.limit_bytes)&&quota.limit_bytes>0?quota.limit_bytes:null;
  const denominator=Math.max(limit||0,total);
  if(limit!==null) rows.push({key:'free',label:'Libre',color:'#e9eee5',bytes:Math.max(0,limit-total)});
  return {known,total,denominator,inconsistent,discrepancy:inconsistent,segments:rows.map(item=>({...item,width:denominator>0?item.bytes/denominator*100:0}))};
}
function totals(items, selected, quota) {
  const picked = items.filter(i => selected.has(i.id) && i.selectable);
  const bytes = picked.reduce((n,i) => n + i.reclaimable_bytes, 0);
  const used = quota?.used_bytes;
  const limit = quota?.limit_bytes;
  const projectedUsed = used == null ? null : Math.max(0, used - bytes);
  return {picked, bytes, quotaPercent:percent(bytes,limit), usedPercent:percent(bytes,used),
    projectedUsed, projectedFree:limit == null || projectedUsed == null ? null : Math.max(0,limit - projectedUsed)};
}
function filterItems(items, {category='all',search='',age=0,sort='size',now=Date.now()/1000}={}) {
  const query=search.toLocaleLowerCase('es');
  return items.filter(i => (category === 'all' || i.category === category)
    && (!query || `${i.name} ${i.path} ${i.account || ''}`.toLocaleLowerCase('es').includes(query))
    && (!age || now - i.modified_at > age * 86400))
    .sort((a,b) => sort === 'oldest' ? a.modified_at - b.modified_at : sort === 'name'
      ? a.name.localeCompare(b.name,'es') : b.reclaimable_bytes - a.reclaimable_bytes);
}

// Group presentation only. Selections and deletion requests keep each exact item.
function candidateRows(items, options={}) {
  const rows=[], groups=new Map();
  for (const item of filterItems(items.filter(i=>i.selectable), options)) {
    if (!['trash','spam','mail_trash'].includes(item.category)) {
      rows.push({...item,members:[item],grouped:false});
      continue;
    }
    const account=item.account || '';
    const key=JSON.stringify([item.category,account]);
    let group=groups.get(key);
    if (!group) {
      group={...item,id:`group:${key}`,name:item.category==='trash'?'Papelera de archivos':`${categories[item.category]} · ${account || 'Cuenta principal'}`,
        members:[],grouped:true,size_bytes:0,reclaimable_bytes:0,modified_at:0,paths:[]};
      groups.set(key,group);rows.push(group);
    }
    group.members.push(item);group.paths.push(item.path);
    group.size_bytes+=item.size_bytes;group.reclaimable_bytes+=item.reclaimable_bytes;
    group.modified_at=Math.max(group.modified_at,item.modified_at);
    if (item.risk==='review') group.risk='review';
  }
  for (const group of groups.values()) {
    const parents=group.paths.map(path=>path.split('/').slice(0,-1));
    // Keep a true shared prefix, even if later path components happen to match.
    let length=0;while(length<parents[0].length && parents.every(path=>path[length]===parents[0][length]))length++;
    group.path=parents[0].slice(0,length).join('/')+'/';
    group.relative_path=group.name;
    group.reason=`${group.members.length} ${group.category==='trash'?'archivos':'mensajes'} incluidos según los filtros actuales. Cada elemento se valida antes de borrar.`;
    delete group.paths;
  }
  return filterItems(rows,{sort:options.sort || 'size'});
}


function progressState(completed, total) {
  if (!Number.isFinite(total) || total <= 0) return {percent:null, completed:0, total:0};
  const done = Math.min(total, Math.max(0, Number.isFinite(completed) ? completed : 0));
  return {percent:Math.floor(done / total * 100), completed:done, total};
}

function groupFailures(failures = []) {
  const groups = new Map();
  for (const failure of failures) {
    const reason = String(failure.error || 'No se pudo completar la operación.');
    if (!groups.has(reason)) groups.set(reason, {reason, count:0, examples:[]});
    const group = groups.get(reason);
    group.count++;
    if (group.examples.length < 3 && failure.path) group.examples.push(String(failure.path));
  }
  return [...groups.values()];
}

function resultSummary({deleted=0, bytes=0, failed=0, mail=false, demo=false, interrupted=false}) {
  const single = mail ? 'correo' : 'elemento', plural = mail ? 'correos' : 'elementos';
  const prefix = demo ? 'Simulación: ' : '';
  if (interrupted) return `${prefix}Proceso interrumpido. ${deleted} eliminaciones confirmadas. Vuelve a analizar antes de reintentar.`;
  const result = deleted ? `${deleted} ${deleted === 1 ? single + ' eliminado' : plural + ' eliminados'}. ${formatBytes(bytes)} de ahorro estimado.` : `No se eliminó ningún ${single}.`;
  return `${prefix}${result}${failed ? ` ${failed} pendientes. Vuelve a analizar.` : ''}`;
}

function renderDetails(container, messages = [], failures = []) {
  const doc = container.ownerDocument;
  const notes = [...new Set(messages.filter(Boolean).map(String))];
  const groups = groupFailures(failures);
  container.replaceChildren();
  container.hidden = !notes.length && !groups.length;
  if (container.hidden) return;
  const details = doc.createElement('details');
  details.className = 'result-details';
  const summary = doc.createElement('summary');
  summary.textContent = failures.length ? `Ver detalles · ${failures.length} pendientes` : `Ver avisos · ${notes.length}`;
  const content = doc.createElement('div');
  content.className = 'result-details-content';
  for (const text of notes.slice(0, 5)) {
    const paragraph = doc.createElement('p'); paragraph.textContent = text; content.append(paragraph);
  }
  for (const group of groups.slice(0, 5)) {
    const paragraph = doc.createElement('p');
    const reason = doc.createElement('strong'); reason.textContent = `${group.count} · ${group.reason}`;
    paragraph.append(reason); content.append(paragraph);
    const examples = doc.createElement('ul');
    for (const path of group.examples) { const item = doc.createElement('li'); item.textContent = path; examples.append(item); }
    content.append(examples);
  }
  const hint = doc.createElement('p'); hint.textContent = 'Aquí se muestran motivos agrupados y algunos ejemplos. El informe incluye todos los detalles.';
  content.append(hint);
  const download = doc.createElement('button');
  download.type = 'button'; download.className = 'text-button'; download.textContent = 'Descargar informe completo';
  download.addEventListener('click', () => {
    const report = [...notes, ...failures.map(f => `${f.path || ''}: ${f.error || ''}`)].join('\n');
    const url = URL.createObjectURL(new Blob([report], {type:'text/plain;charset=utf-8'}));
    const anchor = doc.createElement('a'); anchor.href = url; anchor.download = 'cct-resultados.txt'; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  details.append(summary, content, download); container.append(details);
}

function createActivity(doc = document) {
  const dialog = doc.getElementById('activity');
  const active = new Map();
  dialog.addEventListener('cancel', event => { if (active.size) event.preventDefault(); });
  function render() {
    doc.querySelector('main').setAttribute('aria-busy', String(active.size > 0));
    if (!active.size) { if (dialog.open) dialog.close(); return; }
    const state = [...active.values()].at(-1);
    const progress = progressState(state.completed, state.total);
    doc.getElementById('activity-title').textContent = state.title;
    doc.getElementById('activity-message').textContent = state.message;
    const bar = doc.getElementById('activity-progress');
    const label = doc.getElementById('activity-count');
    if (bar) bar.hidden = progress.percent === null;
    if (label) label.hidden = progress.percent === null;
    if (progress.percent !== null) {
      if (bar) bar.value = progress.percent;
      if (label) label.textContent = `${progress.percent} % · ${progress.completed.toLocaleString('es-CL')} de ${progress.total.toLocaleString('es-CL')} procesados`;
    }
    if (!dialog.open) dialog.showModal();
  }
  return {
    begin(state) { const ticket = Symbol(); active.set(ticket, state); render(); return ticket; },
    update(ticket, state) { if (active.has(ticket)) { active.set(ticket, {...active.get(ticket), ...state}); render(); } },
    end(ticket) { active.delete(ticket); render(); }
  };
}

const $ = id => document.getElementById(id);
const state = {
  accounts: [], filter: '', sort: 'usage', version: '', user: null, items: [], selected: new Set(), quota: {},
  category: 'all', page: 0, pageSize: 9, busy: false, analysisToken: null, scanJob: null, scanStop: false, scannedAt: 0
};
const activity = createActivity();
const icons = {trash: '♧', logs: '≡', backups: '▤', spam: '✉', mail_trash: '✉'};
const CHART_LIMIT = 12;

function parseDisk(value) {
  if (value == null) return null;
  const text = String(value).trim();
  if (!text || /^(unlimited|none|null|n\/a|-)$/i.test(text)) return null;
  const match = text.match(/^([\d.,]+)\s*([KMGTP]i?B?|B)?$/i);
  if (!match) {
    const raw = Number(text);
    return Number.isFinite(raw) && raw >= 0 ? raw : null;
  }
  const amount = Number(match[1].replace(',', '.'));
  if (!Number.isFinite(amount) || amount < 0) return null;
  const unit = (match[2] || 'B').toUpperCase();
  const factor = {
    B: 1, K: 1024, KB: 1024, KIB: 1024,
    M: 1024 ** 2, MB: 1024 ** 2, MIB: 1024 ** 2,
    G: 1024 ** 3, GB: 1024 ** 3, GIB: 1024 ** 3,
    T: 1024 ** 4, TB: 1024 ** 4, TIB: 1024 ** 4,
    P: 1024 ** 5, PB: 1024 ** 5, PIB: 1024 ** 5
  }[unit];
  return factor ? amount * factor : null;
}

function enrichAccount(row) {
  const usedBytes = parseDisk(row.diskused);
  const limitBytes = parseDisk(row.disklimit);
  const usagePct = usedBytes != null && limitBytes > 0 ? (usedBytes / limitBytes) * 100 : null;
  return {...row, usedBytes, limitBytes, usagePct};
}

function usageTone(pct) {
  if (pct == null) return 'unknown';
  if (pct >= 95) return 'critical';
  if (pct >= 85) return 'high';
  if (pct >= 70) return 'warn';
  return 'ok';
}

function compareText(a, b) {
  return String(a || '').localeCompare(String(b || ''), 'es', {sensitivity: 'base', numeric: true});
}

function sortedAccounts(rows) {
  const list = rows.slice();
  const key = state.sort || 'usage';
  list.sort((a, b) => {
    if (key === 'usage') {
      const ap = a.usagePct == null ? -1 : a.usagePct;
      const bp = b.usagePct == null ? -1 : b.usagePct;
      return bp - ap || compareText(a.user, b.user);
    }
    if (key === 'disk') {
      const au = a.usedBytes == null ? -1 : a.usedBytes;
      const bu = b.usedBytes == null ? -1 : b.usedBytes;
      return bu - au || compareText(a.user, b.user);
    }
    if (key === 'domain') return compareText(a.domain, b.domain) || compareText(a.user, b.user);
    if (key === 'plan') return compareText(a.plan, b.plan) || compareText(a.user, b.user);
    return compareText(a.user, b.user);
  });
  return list;
}

function setFooterVersion(version) {
  state.version = version || state.version || '';
  $('footer-version').textContent = state.version ? `CCT v${state.version}` : 'CCT';
}

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
  const filtered = state.accounts.filter(row => !query || `${row.user} ${row.domain} ${row.plan}`.toLocaleLowerCase('es').includes(query));
  return sortedAccounts(filtered);
}

function renderUsageSummary(rows) {
  const known = rows.filter(row => row.usagePct != null);
  const critical = known.filter(row => row.usagePct >= 95).length;
  const high = known.filter(row => row.usagePct >= 85 && row.usagePct < 95).length;
  $('usage-summary').replaceChildren(
    node('span', 'usage-pill', `${rows.length} cuentas`),
    node('span', 'usage-pill warn', `${high} ≥ 85 %`),
    node('span', 'usage-pill critical', `${critical} ≥ 95 %`)
  );
  $('usage-chart-caption').textContent = known.length
    ? `Top ${Math.min(CHART_LIMIT, known.length)} por porcentaje del plan.`
    : 'Sin datos de cuota para graficar.';
}

function renderUsageChart(rows) {
  const chart = $('usage-chart');
  chart.replaceChildren();
  const ranked = rows
    .filter(row => row.usagePct != null)
    .slice()
    .sort((a, b) => (b.usagePct - a.usagePct) || compareText(a.user, b.user))
    .slice(0, CHART_LIMIT);
  if (!ranked.length) {
    chart.append(node('p', 'usage-chart-empty', 'No hay porcentajes de uso disponibles.'));
    return;
  }
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const rowHeight = 28;
  const labelWidth = 118;
  const width = 720;
  const height = ranked.length * rowHeight + 8;
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('role', 'presentation');
  ranked.forEach((row, index) => {
    const y = index * rowHeight + 4;
    const pct = Math.max(0, Math.min(100, row.usagePct));
    const barMax = width - labelWidth - 70;
    const barWidth = Math.max(2, (pct / 100) * barMax);
    const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    label.setAttribute('x', '0');
    label.setAttribute('y', String(y + 16));
    label.setAttribute('class', 'usage-chart-label');
    label.textContent = row.user;
    const track = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    track.setAttribute('x', String(labelWidth));
    track.setAttribute('y', String(y + 6));
    track.setAttribute('width', String(barMax));
    track.setAttribute('height', '12');
    track.setAttribute('rx', '3');
    track.setAttribute('class', 'usage-chart-track');
    const bar = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    bar.setAttribute('x', String(labelWidth));
    bar.setAttribute('y', String(y + 6));
    bar.setAttribute('width', String(barWidth));
    bar.setAttribute('height', '12');
    bar.setAttribute('rx', '3');
    bar.setAttribute('class', `usage-chart-bar tone-${usageTone(pct)}`);
    const value = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    value.setAttribute('x', String(labelWidth + barMax + 8));
    value.setAttribute('y', String(y + 16));
    value.setAttribute('class', 'usage-chart-value');
    value.textContent = formatPercent(pct);
    svg.append(label, track, bar, value);
  });
  chart.append(svg);
}

function renderAccountList() {
  const rows = visibleAccounts();
  renderUsageSummary(state.accounts);
  renderUsageChart(state.filter ? rows : state.accounts);
  $('account-list').replaceChildren();
  $('account-empty').hidden = rows.length > 0;
  for (const row of rows) {
    const tr = node('tr');
    const tone = usageTone(row.usagePct);
    if (tone === 'critical' || tone === 'high') tr.classList.add(`row-${tone}`);
    const usage = node('td', 'usage-cell');
    if (row.usagePct == null) usage.append(node('span', 'usage-na', '—'));
    else {
      const pct = Math.max(0, Math.min(100, row.usagePct));
      const meter = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      meter.setAttribute('class', `usage-meter tone-${tone}`);
      meter.setAttribute('viewBox', '0 0 100 8');
      meter.setAttribute('aria-hidden', 'true');
      const track = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      track.setAttribute('x', '0'); track.setAttribute('y', '0');
      track.setAttribute('width', '100'); track.setAttribute('height', '8');
      track.setAttribute('rx', '2'); track.setAttribute('class', 'usage-meter-track');
      const fill = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      fill.setAttribute('x', '0'); fill.setAttribute('y', '0');
      fill.setAttribute('width', String(Math.max(2, pct))); fill.setAttribute('height', '8');
      fill.setAttribute('rx', '2'); fill.setAttribute('class', 'usage-meter-fill');
      meter.append(track, fill);
      usage.append(meter, node('strong', '', formatPercent(pct)));
    }
    tr.append(
      node('td', '', row.user),
      node('td', '', row.domain || '—'),
      node('td', '', row.plan || '—'),
      usage,
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
    state.accounts = (data.accounts || []).map(enrichAccount);
    setFooterVersion(data.version);
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
$('account-sort').addEventListener('change', () => {
  state.sort = $('account-sort').value || 'usage';
  renderAccountList();
});
$('back-list').addEventListener('click', backToList);
setFooterVersion('');
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
