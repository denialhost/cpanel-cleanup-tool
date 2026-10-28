export const categories = {all:'Todos',trash:'Papelera',logs:'Registros',backups:'Respaldos',spam:'Spam',mail_trash:'Papelera de correo'};
export const formatBytes = value => {
  if (value == null || !Number.isFinite(value)) return '—';
  if (value === 0) return '0 B';
  const n = Math.min(4, Math.max(0, Math.floor(Math.log(Math.abs(value)) / Math.log(1024))));
  return `${new Intl.NumberFormat('es-CL',{maximumFractionDigits:n ? 2 : 0}).format(value / 1024 ** n)} ${['B','KiB','MiB','GiB','TiB'][n]}`;
};
export const percent = (value, total) => total > 0 && value != null ? value / total * 100 : null;
export const formatPercent = value => value == null ? '—' : `${new Intl.NumberFormat('es-CL',{maximumFractionDigits:1}).format(value)} %`;
export const storageCategories = {
  mail:{label:'Correo',color:'#4f82c9'}, web:{label:'Web',color:'#9274c7'},
  databases:{label:'Bases de datos',color:'#d6a044'}, backups:{label:'Respaldos',color:'#6f9c69'},
  logs:{label:'Registros',color:'#c77e66'}, other:{label:'Otros',color:'#8a969a'}
};
export function storageSegments(quota, storage) {
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

// Group presentation only. Selections and deletion requests keep each exact item.
export function candidateRows(items, options={}) {
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
