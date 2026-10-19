export const categories = {all:'Todos',trash:'Papelera',logs:'Registros',backups:'Respaldos',spam:'Spam',mail_trash:'Papelera de correo'};
export const formatBytes = value => {
  if (value == null || !Number.isFinite(value)) return '—';
  if (value === 0) return '0 B';
  const n = Math.min(4, Math.max(0, Math.floor(Math.log(Math.abs(value)) / Math.log(1024))));
  return `${new Intl.NumberFormat('es-CL',{maximumFractionDigits:n ? 2 : 0}).format(value / 1024 ** n)} ${['B','KiB','MiB','GiB','TiB'][n]}`;
};
export const percent = (value, total) => total > 0 && value != null ? value / total * 100 : null;
export const formatPercent = value => value == null ? '—' : `${new Intl.NumberFormat('es-CL',{maximumFractionDigits:1}).format(value)} %`;
export function totals(items, selected, quota) {
  const picked = items.filter(i => selected.has(i.id) && i.selectable);
  const bytes = picked.reduce((n,i) => n + i.reclaimable_bytes, 0);
  const used = quota?.used_bytes;
  const limit = quota?.limit_bytes;
  const projectedUsed = used == null ? null : Math.max(0, used - bytes);
  return {picked, bytes, quotaPercent:percent(bytes,limit), usedPercent:percent(bytes,used),
    projectedUsed, projectedFree:limit == null || projectedUsed == null ? null : Math.max(0,limit - projectedUsed)};
}
export function filterItems(items, {category='all',search='',age=0,sort='size',now=Date.now()/1000}={}) {
  const query=search.toLocaleLowerCase('es');
  return items.filter(i => (category === 'all' || i.category === category)
    && (!query || `${i.name} ${i.path} ${i.account || ''}`.toLocaleLowerCase('es').includes(query))
    && (!age || now - i.modified_at > age * 86400))
    .sort((a,b) => sort === 'oldest' ? a.modified_at - b.modified_at : sort === 'name'
      ? a.name.localeCompare(b.name,'es') : b.reclaimable_bytes - a.reclaimable_bytes);
}
