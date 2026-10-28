import {categories, formatBytes, formatPercent, percent, totals, candidateRows, storageSegments} from './model.js?v=0.2.5-storage2';
import {setupMailCleanup} from './mail.js?v=0.2.5-storage2';
import {createActivity, renderDetails, resultSummary} from './activity.js?v=0.2.5-storage2';
const $ = id => document.getElementById(id);
const demo = document.body.dataset.demo === 'true';
const state={items:[],selected:new Set(),quota:{},category:'all',page:0,pageSize:9,busy:false,scannedAt:0,storage:{status:'loading'},storageStale:false};
let storageSequence=0;
let mailControls;
const activity = createActivity();
const icons={trash:'♧',logs:'≡',backups:'▤',spam:'✉',mail_trash:'✉'};
const riskText={candidate:'Candidato',review:'Revisar',protected:'Protegido'};
function node(tag, cls, text) { const el=document.createElement(tag); if(cls)el.className=cls;if(text!=null)el.textContent=text;return el; }
function memberCount(item){const count=item.members.length;return `${count} ${item.category==='trash'?(count===1?'archivo':'archivos'):(count===1?'mensaje':'mensajes')}`;}
function message(text,error=false){$('notice').textContent=text;$('notice').classList.toggle('error',error);$('notice').hidden=false;}
function warnings(messages=[],failures=[]){renderDetails($('warnings'),messages,failures);}
function cacheNotice(refresh){
  if(!refresh)return;
  $('cache-notice').hidden=false;$('cache-message').textContent=refresh.message;
  const minutes=Math.ceil((refresh.retry_after_seconds||300)/60);
  $('cache-retry').textContent=`Si aún no ves el cambio, vuelve a consultar en ${minutes} minutos. Es un intervalo sugerido; el plazo de actualización depende del servidor.`;
}
async function request(action,body){
  const url=demo ? new URL(`/api/${action}`,location.href) : new URL(location.href);
  if(!demo){url.search='';url.searchParams.set('action',action);}
  const response=await fetch(url,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:body?{'Content-Type':'application/json','X-CCT-Request':'1'}:{},body:body?JSON.stringify(body):undefined});
  let data;try{data=await response.json();}catch{throw new Error('La sesión de cPanel pudo haber expirado. Vuelve a entrar al plugin.');}
  if(!response.ok || data.error)throw new Error(data.error || 'No se pudo completar la solicitud.');
  return data;
}
function filters(){return {category:state.category,search:$('search').value,age:Number($('age').value),sort:$('sort').value};}
function visible(){return candidateRows(state.items,filters());}
function renderStorage(){
  const report=storageSegments(state.quota,state.storage);
  $('storage-bar').replaceChildren();$('storage-legend').replaceChildren();
  const descriptions=[];
  for(const item of report.segments){
    const text=`${item.label}: ${formatBytes(item.bytes)}`;
    const segment=node('span','storage-segment');segment.style.width=`${item.width}%`;segment.style.backgroundColor=item.color;segment.title=text;
    $('storage-bar').append(segment);
    const entry=node('span','storage-key'),dot=node('i','storage-dot');dot.style.backgroundColor=item.color;
    entry.append(dot,node('span','',item.label),node('strong','',formatBytes(item.bytes)));$('storage-legend').append(entry);descriptions.push(text);
  }
  $('storage-bar').setAttribute('aria-label',descriptions.join('. ')||'Consultando uso por categoría');
  $('storage-bar').setAttribute('aria-busy',String(state.storage.status==='loading'));
  $('storage-refresh').disabled=state.storage.status==='loading';
  $('usage-caption').textContent=state.quota.used_bytes==null?'Uso no disponible':state.quota.limit_bytes?`${formatPercent(percent(state.quota.used_bytes,state.quota.limit_bytes))} ocupado actualmente`:'Uso actual';
  $('storage-note').textContent=state.storage.status==='loading'?'Consultando el uso por categoría…':report.inconsistent?
    'El desglose no coincide con la cuota y no se muestra. La barra conserva el uso y el espacio libre informados por cPanel.':!report.known?
    'El desglose no está disponible por ahora. La barra muestra el uso total informado por cPanel.':
    'Uso físico en disco. Web incluye public_html y public_ftp; Otros reúne los archivos sin categoría. Las cifras pueden actualizarse con retraso.';
}
async function loadStorage(){
  const sequence=++storageSequence;state.storageStale=false;state.storage={status:'loading'};renderStorage();
  try{const data=await request('storage');if(sequence===storageSequence)state.storage=data.storage;}
  catch{if(sequence===storageSequence)state.storage={status:'unavailable'};}
  finally{if(sequence===storageSequence)renderStorage();}
}
function renderQuota(){
  const q=state.quota;
  $('quota-limit').textContent=q.limit_bytes == null ? q.source ? 'No definido' : '—' : formatBytes(q.limit_bytes);
  $('quota-used').textContent=formatBytes(q.used_bytes);
  $('quota-free').textContent=formatBytes(q.free_bytes);
  $('quota-used-percent').textContent=q.limit_bytes ? `${formatPercent(percent(q.used_bytes,q.limit_bytes))} de tu cuota` : q.error ? 'Cuota no disponible' : 'Uso informado por cPanel';
  $('quota-free-percent').textContent=q.limit_bytes ? `${formatPercent(percent(q.free_bytes,q.limit_bytes))} disponible` : 'Sin porcentaje de cuota disponible';
  const candidates=state.items.filter(i=>i.selectable);
  const total=candidates.reduce((n,i)=>n+i.reclaimable_bytes,0);
  $('recoverable').textContent=formatBytes(total);
  $('recoverable-detail').textContent=`${candidates.length} candidatos · ahorro estimado`;
  $('usage-caption').textContent=q.limit_bytes ? `${formatPercent(percent(q.used_bytes,q.limit_bytes))} ocupado actualmente` : 'Sin cuota finita disponible';
}
function renderTabs(){
  $('categories').replaceChildren();
  const rows=candidateRows(state.items);
  for(const [key,label] of Object.entries(categories)){
    const button=node('button','category-tab',label);button.type='button';button.setAttribute('aria-pressed',String(state.category===key));
    button.append(node('span','',String(key==='all'?rows.length:rows.filter(i=>i.category===key).length)));
    button.disabled=state.busy;
    button.addEventListener('click',()=>{state.category=key;state.page=0;renderTabs();renderTable();renderSelection();});
    $('categories').append(button);
  }
}
function renderTable(){
  const filtered=visible(),pages=Math.max(1,Math.ceil(filtered.length/state.pageSize));state.page=Math.min(state.page,pages-1);
  const page=filtered.slice(state.page*state.pageSize,(state.page+1)*state.pageSize);
  $('file-list').replaceChildren();
  for(const item of page){
    const selected=item.members.filter(member=>state.selected.has(member.id)).length;
    const row=node('tr',item.grouped?'is-group':'');row.classList.toggle('is-selected',selected>0);
    const checkCell=node('td','check-cell'),check=node('input');check.type='checkbox';check.checked=selected===item.members.length;check.indeterminate=selected>0&&!check.checked;check.disabled=state.busy;check.setAttribute('aria-label',`Seleccionar ${item.relative_path}`);check.title=item.reason;
    check.addEventListener('change',()=>{for(const member of item.members)check.checked?state.selected.add(member.id):state.selected.delete(member.id);renderTable();renderSelection();});checkCell.append(check);
    const fileCell=node('td'),name=node('div','file-name'),info=node('div','file-info');info.append(node('strong','',item.grouped?item.name:item.account?`${item.account} · mensaje ${item.uid}`:item.name));if(item.grouped)info.append(node('span','group-count',memberCount(item)));const path=node('code','',item.path);path.title=item.path;info.append(path);name.append(node('span',`file-symbol ${item.category}`,icons[item.category]||'▤'),info);fileCell.append(name);fileCell.title=item.reason;
    const days=Math.max(0,Math.floor((Date.now()/1000-item.modified_at)/86400));
    const date=node('td','modified',new Date(item.modified_at*1000).toLocaleDateString('es-CL',{day:'2-digit',month:'short',year:'numeric'}));date.append(node('small','',`${days} ${days===1?'día':'días'} atrás`));
    const size=node('td','size-cell size-value',formatBytes(item.reclaimable_bytes));size.title=`Tamaño lógico: ${formatBytes(item.size_bytes)}. Ahorro estimado según bloques de disco.`;
    const risk=node('td'),badge=node('span',`badge ${item.risk}`,riskText[item.risk]);badge.title=item.reason;risk.append(badge);row.append(checkCell,fileCell,date,size,risk);$('file-list').append(row);
  }
  $('empty').hidden=filtered.length>0;
  const elements=filtered.reduce((sum,row)=>sum+row.members.length,0);
  $('visible-count').textContent=`${filtered.length} filas · ${elements} elementos${state.category!=='all'?` en ${categories[state.category].toLocaleLowerCase('es')}`:''}`;
  $('page-info').textContent=filtered.length?`Mostrando ${state.page*state.pageSize+1}–${Math.min((state.page+1)*state.pageSize,filtered.length)} de ${filtered.length}`:'Sin resultados';
  $('previous-page').disabled=state.busy||!state.page;$('next-page').disabled=state.busy||state.page>=pages-1;
  updateSelectAll();
}
function updateSelectAll(){
  const eligible=visible().flatMap(row=>row.members),count=eligible.filter(i=>state.selected.has(i.id)).length;
  $('select-all').checked=eligible.length>0&&count===eligible.length;
  $('select-all').indeterminate=count>0&&count<eligible.length;
  $('select-all').disabled=state.busy||!eligible.length;
}
function renderSelection(){
  const t=totals(state.items,state.selected,state.quota);
  $('selected-size').textContent=formatBytes(t.bytes);$('selected-quota-percent').textContent=formatPercent(t.quotaPercent);$('selected-used-percent').textContent=formatPercent(t.usedPercent);
  $('projected-free').textContent=formatBytes(t.projectedFree);
  $('selected-count').textContent=t.picked.length?`${t.picked.length} elementos seleccionados`:'Ningún elemento seleccionado';
  const shownIds=new Set(visible().flatMap(row=>row.members.map(i=>i.id)));const hidden=t.picked.filter(i=>!shownIds.has(i.id)).length;
  $('hidden-selected').textContent=hidden?`${hidden} fuera del filtro actual`:'';
  $('review').disabled=state.busy||!t.picked.length;
  $('clear-selection').disabled=state.busy||!t.picked.length;
  $('selection-breakdown').replaceChildren();
  for(const [key,label] of Object.entries(categories)){
    if(key==='all')continue;
    const bytes=t.picked.filter(i=>i.category===key).reduce((n,i)=>n+i.reclaimable_bytes,0);
    const row=node('div','breakdown-row');row.append(node('span','',label),node('strong','',formatBytes(bytes)));$('selection-breakdown').append(row);
  }
  const limit=state.quota.limit_bytes;
  const used=state.quota.used_bytes;
  const current=limit>0 && used!=null ? Math.min(100,Math.max(0,used/limit*100)) : 0;
  const projected=limit>0&&t.projectedUsed!=null ? Math.min(100,Math.max(0,t.projectedUsed/limit*100)) : 0;
  // Over-quota usage must be removed before the bar can show newly free quota.
  $('bar-used').style.width=`${projected}%`;$('bar-selected').style.width=`${Math.max(0,current-projected)}%`;
  $('usage-bar').setAttribute('aria-label',`Ocupado actual ${formatBytes(used)}, selección ${formatBytes(t.bytes)}, libre estimado después de limpiar ${formatBytes(t.projectedFree)}`);
  $('projected-label').textContent=t.picked.length&&limit?`Quedaría ${formatPercent(percent(t.projectedFree,limit))} libre · estimado`:'Selecciona archivos para ver el cambio';
  $('usage-projection').hidden=!t.picked.length;
  renderStorage();
}
function render(){renderQuota();renderTabs();renderTable();renderSelection();}
function busy(value){state.busy=value;document.body.classList.toggle('loading',value);$('scan').disabled=value;$('refresh-quota').disabled=value;$('reset-demo').disabled=value;for(const id of ['search','age','sort'])$(id).disabled=value;mailControls?.setBusy(value);render();if(!value&&state.storageStale&&state.scannedAt)loadStorage();}
async function scan(){
  if(state.busy)return;
  const ticket=activity.begin({title:'Generando tu análisis',message:'Espera mientras revisamos tus archivos y correos.'});
  busy(true);$('notice').hidden=true;
  try{const data=await request('scan');state.items=data.items;state.selected.clear();state.quota=data.quota;state.scannedAt=data.scanned_at;state.storageStale=true;state.page=0;$('account-name').textContent=data.account.username;
    $('scan-meta').textContent=`${data.partial?'Análisis parcial':'Último análisis'} · ${new Date(data.scanned_at*1000).toLocaleString('es-CL')} · ${data.visited.toLocaleString('es-CL')} archivos revisados`;
    warnings([...data.warnings,...(data.quota.error?[data.quota.error]:[])]);
  }catch(err){message(err.message,true);if(!state.scannedAt)$('account-name').textContent='Cuenta pendiente de analizar';}finally{busy(false);activity.end(ticket);}
}
function review(){
  const t=totals(state.items,state.selected,state.quota);if(!t.picked.length)return;
  $('confirm-summary').textContent=`Vas a eliminar ${t.picked.length} elementos. Ahorro estimado: ${formatBytes(t.bytes)} (${formatPercent(t.quotaPercent)} de tu cuota).`;
  $('confirm-list').replaceChildren();
  for(const item of candidateRows(t.picked)){const row=node('div','confirm-item');row.append(node('span','',item.grouped?`${item.name} · ${memberCount(item)} · ${item.path}`:item.path),node('strong','',formatBytes(item.reclaimable_bytes)));$('confirm-list').append(row);}
  $('backup-ack-row').hidden=!t.picked.some(i=>i.category==='backups');$('backup-ack').checked=false;$('confirm-word').value='';validateConfirm();$('confirmation').showModal();$('confirm-word').focus();
}
function validateConfirm(){ $('confirm-delete').disabled=state.busy||$('confirm-word').value!=='ELIMINAR'||(!$('backup-ack-row').hidden&&!$('backup-ack').checked); }
async function performDelete(event){
  event.preventDefault();if($('confirm-delete').disabled)return;
  const items=totals(state.items,state.selected,state.quota).picked;
  const backupAck=$('backup-ack').checked;
  mailControls?.invalidate();
  $('confirmation').close();busy(true);
  const ticket=activity.begin({title:demo?'Simulando limpieza':'Liberando espacio',message:'Validando la selección y procesando los elementos confirmados.',completed:0,total:items.length});
  let deleted=0,estimated=0,failures=[];const notices=[];
  try{
    for(let offset=0;offset<items.length;offset+=250){
      const result=await request('delete',{items:items.slice(offset,offset+250),confirm:'ELIMINAR',backups_ack:backupAck});
      const ids=new Set(result.deleted_ids);deleted+=ids.size;estimated+=result.estimated_removed_bytes;failures.push(...result.failed);
      state.items=state.items.filter(i=>!ids.has(i.id));for(const id of ids)state.selected.delete(id);state.quota=result.quota;
      for(const failure of result.failed)state.selected.delete(failure.id);
      notices.push(...(result.warnings||[]));if(result.quota?.error)notices.push(result.quota.error);render();
      cacheNotice(result.cache_refresh);
      activity.update(ticket,{completed:Math.min(offset+250,items.length),message:`${deleted} eliminados · ${failures.length} pendientes. Continuamos con tu selección.`});
    }
    message(resultSummary({deleted,bytes:estimated,failed:failures.length,demo}),!!failures.length);
    warnings(notices,failures);
  }catch(err){message(resultSummary({deleted,demo,interrupted:true}),true);warnings([...notices,err.message],failures);state.selected.clear();}
  finally{state.storageStale=true;busy(false);activity.end(ticket);}
}
$('scan').addEventListener('click',scan);
$('storage-refresh').addEventListener('click',loadStorage);
$('refresh-quota').textContent='Refrescar uso en cPanel ↻';
$('refresh-quota').addEventListener('click',async()=>{if(state.busy)return;const ticket=activity.begin({title:'Actualizando uso de disco',message:'Espera mientras consultamos la cuota de tu cuenta.'});busy(true);try{const data=await request('refresh',{});state.quota=data.quota;state.storageStale=true;cacheNotice(data.cache_refresh);data.quota.error?message('No se pudo consultar la cuota. Revisa los detalles.',true):message('Cuota actualizada.');warnings(data.quota.error?[data.quota.error]:[]);}catch(err){message('No se pudo actualizar la cuota. Revisa los detalles.',true);warnings([err.message]);}finally{busy(false);activity.end(ticket);}});
$('select-all').addEventListener('change',()=>{for(const item of visible().flatMap(row=>row.members))$('select-all').checked?state.selected.add(item.id):state.selected.delete(item.id);renderTable();renderSelection();});
$('clear-selection').addEventListener('click',()=>{state.selected.clear();renderTable();renderSelection();});
for(const id of ['search','age','sort'])$(id).addEventListener(id==='search'?'input':'change',()=>{state.page=0;renderTable();renderSelection();});
$('previous-page').addEventListener('click',()=>{state.page--;renderTable();});$('next-page').addEventListener('click',()=>{state.page++;renderTable();});
$('review').addEventListener('click',review);for(const button of document.querySelectorAll('.close-dialog'))button.addEventListener('click',()=>$('confirmation').close());
$('confirm-word').addEventListener('input',validateConfirm);$('backup-ack').addEventListener('change',validateConfirm);$('confirm-form').addEventListener('submit',performDelete);
if(demo){$('demo-banner').hidden=false;$('reset-demo').addEventListener('click',async()=>{try{await request('reset',{});mailControls?.invalidate();scan();}catch(err){message(err.message,true);}});}
mailControls=setupMailCleanup({request,demo,isBusy:()=>state.busy,setBusy:busy,cacheNotice,activity,
  onQuota(quota){state.quota=quota;render();},
  onResult(result){const ids=new Set(result.deleted_ids);state.items=state.items.filter(i=>!ids.has(i.id));for(const id of ids)state.selected.delete(id);state.quota=result.quota;state.storageStale=true;render();}
});
scan();
