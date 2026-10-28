import {formatBytes} from './model.js?v=0.2.5-storage2';

export function progressState(completed, total) {
  if (!Number.isFinite(total) || total <= 0) return {percent:null, completed:0, total:0};
  const done = Math.min(total, Math.max(0, Number.isFinite(completed) ? completed : 0));
  return {percent:Math.floor(done / total * 100), completed:done, total};
}

export function groupFailures(failures = []) {
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

export function resultSummary({deleted=0, bytes=0, failed=0, mail=false, demo=false, interrupted=false}) {
  const single = mail ? 'correo' : 'elemento', plural = mail ? 'correos' : 'elementos';
  const prefix = demo ? 'Simulación: ' : '';
  if (interrupted) return `${prefix}Proceso interrumpido. ${deleted} eliminaciones confirmadas. Vuelve a analizar antes de reintentar.`;
  const result = deleted ? `${deleted} ${deleted === 1 ? single + ' eliminado' : plural + ' eliminados'}. ${formatBytes(bytes)} de ahorro estimado.` : `No se eliminó ningún ${single}.`;
  return `${prefix}${result}${failed ? ` ${failed} pendientes. Vuelve a analizar.` : ''}`;
}

export function renderDetails(container, messages = [], failures = []) {
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

export function createActivity(doc = document) {
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
    bar.hidden = progress.percent === null;
    const label = doc.getElementById('activity-count');
    label.hidden = progress.percent === null;
    if (progress.percent !== null) {
      bar.value = progress.percent;
      label.textContent = `${progress.percent} % · ${progress.completed.toLocaleString('es-CL')} de ${progress.total.toLocaleString('es-CL')} procesados`;
    }
    if (!dialog.open) dialog.showModal();
  }
  return {
    begin(state) { const ticket = Symbol(); active.set(ticket, state); render(); return ticket; },
    update(ticket, state) { if (active.has(ticket)) { active.set(ticket, {...active.get(ticket), ...state}); render(); } },
    end(ticket) { active.delete(ticket); render(); }
  };
}
