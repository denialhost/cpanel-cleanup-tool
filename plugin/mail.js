import {formatBytes,formatPercent,percent} from './model.js?v=0.2.5-storage2';
import {localDateString,presetDate,cutoffFromDate,cutoffLabel,canConfirmMail} from './mail-model.js?v=0.2.5-storage2';
import {renderDetails,resultSummary} from './activity.js?v=0.2.5-storage2';
const folderLabel=name=>{
  const short=name.startsWith('INBOX.')?name.slice(6):name;
  return ({inbox:'Recibidos',sent:'Enviados',drafts:'Borradores',trash:'Papelera',spam:'Spam',junk:'Spam',archive:'Archivo'})[short.toLowerCase()]||short.replaceAll('.',' / ');
};

export function setupMailCleanup(host) {
  const $=id=>document.getElementById(id);
  const state={accounts:[],selected:new Set(),preview:null,loading:true,busy:host.isBusy()};
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
  const scope=()=>document.querySelector('input[name="mail-scope"]:checked').value;
  function notice(text,error=false){$('mail-notice').textContent=text;$('mail-notice').classList.toggle('error',error);$('mail-notice').hidden=false;}
  function invalidate(){state.preview=null;$('mail-preview-result').hidden=true;$('mail-preview-empty').hidden=false;$('mail-review').disabled=true;$('mail-notice').hidden=true;}
  function currentCutoff(){return cutoffFromDate($('mail-date').value);}
  function updateDate(){
    $('mail-date').max=localDateString();$('mail-date').min='1970-01-02';
    if($('mail-preset').value!=='custom')$('mail-date').value=presetDate($('mail-preset').value);
    try{$('mail-cutoff-help').textContent=`Antes del ${cutoffLabel(currentCutoff())}. Se conservan los mensajes de ese momento en adelante. Se usa la fecha interna del mensaje almacenada en el servidor.`;}
    catch(err){$('mail-cutoff-help').textContent=err.message;}
    invalidate();controls();
  }
  function shownAccounts(){const query=$('mail-account-search').value.toLocaleLowerCase('es');return state.accounts.filter(a=>a.address.toLocaleLowerCase('es').includes(query));}
  function renderAccounts(){
    $('mail-accounts').replaceChildren();const shown=shownAccounts();
    for(const account of shown){
      const label=el('label','mail-account'),checkbox=el('input');checkbox.type='checkbox';checkbox.checked=state.selected.has(account.address);checkbox.disabled=state.busy||state.loading;checkbox.setAttribute('aria-label',`Seleccionar correo ${account.address}`);
      const info=el('span','mail-account-info');info.append(el('strong','',account.address));if(account.main)info.append(el('small','','Cuenta principal de cPanel'));
      label.append(checkbox,el('span','mail-account-icon','✉'),info);checkbox.addEventListener('change',()=>{checkbox.checked?state.selected.add(account.address):state.selected.delete(account.address);invalidate();renderAccounts();controls();});$('mail-accounts').append(label);
    }
    if(!shown.length)$('mail-accounts').append(el('p','mail-help',state.loading?'Cargando cuentas…':'No hay cuentas que coincidan.'));
    const ids=new Set(shown.map(a=>a.address)),hidden=[...state.selected].filter(a=>!ids.has(a)).length;
    $('mail-account-count').textContent=`${state.selected.size} de ${state.accounts.length} cuentas seleccionadas${hidden?` · ${hidden} fuera del filtro`:''}`;
    const all=shown.length>0&&shown.every(a=>state.selected.has(a.address));
    $('mail-select-visible').textContent=all?'Desmarcar cuentas visibles':'Seleccionar cuentas visibles';
    $('mail-select-visible').disabled=state.busy||state.loading||!shown.length;
  }
  function controls(){
    for(const id of ['mail-reload','mail-preset','mail-account-search'])$(id).disabled=state.busy||state.loading;
    $('mail-date').disabled=state.busy||state.loading||$('mail-preset').value!=='custom';
    for(const radio of document.querySelectorAll('input[name="mail-scope"]'))radio.disabled=state.busy;
    let valid=true;try{currentCutoff();}catch{valid=false;}
    $('mail-preview').disabled=state.busy||state.loading||!state.selected.size||!valid;
    $('mail-review').disabled=state.busy||!state.preview?.items.length;
    validateConfirm();
  }
  async function loadAccounts(){
    if(host.isBusy()&&!state.loading)return;
    const ticket=host.activity.begin({title:'Preparando tus correos',message:'Espera mientras obtenemos las cuentas de correo.'});
    state.loading=true;invalidate();renderAccounts();controls();
    try{const data=await host.request('mail_accounts');state.accounts=data.accounts;const allowed=new Set(data.accounts.map(a=>a.address));state.selected=new Set([...state.selected].filter(a=>allowed.has(a)));}
    catch(err){notice(err.message,true);}
    finally{state.loading=false;renderAccounts();controls();host.activity.end(ticket);}
  }
  function renderPreview(){
    const result=state.preview;$('mail-preview-empty').hidden=true;$('mail-preview-result').hidden=false;
    $('mail-preview-caption').textContent=`${result.partial?'Vista previa parcial':'Vista previa'} · anteriores al ${cutoffLabel(result.cutoff)} · ${result.scope==='all'?'todas las carpetas':'solo spam y papelera'}.`;
    $('mail-message-total').textContent=result.messages.toLocaleString('es-CL');$('mail-estimate').textContent=formatBytes(result.estimated_bytes);
    const pct=percent(result.estimated_bytes,result.quota?.limit_bytes);
    $('mail-estimate-percent').textContent=pct==null?'El porcentaje de la cuota no está disponible.':`${formatPercent(pct)} de la cuota de tu cuenta · estimado`;
    $('mail-preview-accounts').replaceChildren();
    for(const account of result.accounts){
      const detail=el('details','mail-account-result'),summary=el('summary');summary.append(el('strong','',account.address),el('span','',`${account.messages} correos · ${formatBytes(account.estimated_bytes)}`));detail.append(summary);
      const folders=el('div','mail-folder-list');
      for(const folder of account.folders){const row=el('div','breakdown-row');row.title=folder.name;row.append(el('span','',folderLabel(folder.name)),el('span','',`${folder.messages} · ${formatBytes(folder.bytes)}`));folders.append(row);}
      if(!account.messages)folders.append(el('p','mail-help','No se identificaron mensajes anteriores a la fecha en el alcance elegido.'));
      detail.append(folders);$('mail-preview-accounts').append(detail);
    }
    const warnings=[...(result.warnings||[]),...(result.quota?.error?[result.quota.error]:[])];
    renderDetails($('mail-preview-warnings'),warnings);
    controls();
  }
  async function preview(){
    if(host.isBusy()||$('mail-preview').disabled)return;
    let cutoff;try{cutoff=currentCutoff();}catch(err){notice(err.message,true);return;}
    const ticket=host.activity.begin({title:'Revisando correos antiguos',message:'Espera mientras generamos la vista previa de los mensajes.'});
    host.setBusy(true);invalidate();
    try{state.preview=await host.request('mail_preview',{accounts:[...state.selected],cutoff,scope:scope()});host.onQuota(state.preview.quota);renderPreview();$('mail-notice').hidden=true;}
    catch(err){notice(err.message,true);}finally{host.setBusy(false);host.activity.end(ticket);}
  }
  function review(){
    if(!state.preview?.items.length||host.isBusy())return;
    const result=state.preview;
    $('mail-confirm-summary').textContent=`Se eliminarán ${result.messages} correos anteriores al ${cutoffLabel(result.cutoff)}. Alcance: ${result.scope==='all'?'todas las carpetas':'solo spam y papelera'}. Ahorro estimado: ${formatBytes(result.estimated_bytes)}.${result.partial?' La vista previa es parcial; solo se eliminarán los mensajes identificados.':''}`;
    $('mail-confirm-list').replaceChildren();
    for(const account of result.accounts.filter(a=>a.messages)){
      const row=el('div','confirm-item');row.append(el('span','',`${account.address} · ${account.messages} correos · ${account.folders.map(f=>folderLabel(f.name)).join(', ')}`),el('strong','',formatBytes(account.estimated_bytes)));$('mail-confirm-list').append(row);
    }
    $('mail-irreversible-ack').checked=false;$('mail-all-ack').checked=false;$('mail-all-ack-row').hidden=result.scope!=='all';$('mail-confirm-word').value='';validateConfirm();$('mail-confirmation').showModal();$('mail-irreversible-ack').focus();
  }
  function validateConfirm(){
    $('mail-confirm-delete').disabled=!canConfirmMail({word:$('mail-confirm-word').value,irreversible:$('mail-irreversible-ack').checked,allFolders:state.preview?.scope==='all',allAck:$('mail-all-ack').checked,busy:state.busy});
  }
  async function remove(event){
    event.preventDefault();if($('mail-confirm-delete').disabled||!state.preview)return;
    const frozen=state.preview,ack=$('mail-all-ack').checked;
    $('mail-confirmation').close();host.setBusy(true);
    const ticket=host.activity.begin({title:host.demo?'Simulando borrado':'Eliminando correos',message:'Validando los mensajes confirmados y procesando tu selección.',completed:0,total:frozen.items.length});
    let deleted=0,estimated=0,failures=[];const warnings=[];
    try{
      for(let offset=0;offset<frozen.items.length;offset+=250){
        const result=await host.request('mail_delete',{items:frozen.items.slice(offset,offset+250),cutoff:frozen.cutoff,scope:frozen.scope,confirm:'ELIMINAR CORREOS',irreversible_ack:true,all_folders_ack:ack});
        deleted+=result.deleted_ids.length;estimated+=result.estimated_removed_bytes;failures.push(...result.failed);warnings.push(...(result.warnings||[]));if(result.quota?.error)warnings.push(result.quota.error);host.onResult(result);host.cacheNotice(result.cache_refresh);
        host.activity.update(ticket,{completed:Math.min(offset+250,frozen.items.length),message:`${deleted} eliminados · ${failures.length} pendientes. Continuamos con tu selección.`});
      }
      invalidate();
      notice(resultSummary({deleted,bytes:estimated,failed:failures.length,mail:true,demo:host.demo}),!!failures.length);
      if(failures.length||warnings.length){$('mail-preview-result').hidden=false;$('mail-preview-caption').textContent='Resultado de la operación';$('mail-message-total').textContent=String(deleted);$('mail-estimate').textContent=formatBytes(estimated);$('mail-estimate-percent').textContent='Tamaño estimado de los mensajes eliminados; la cuota real se informa arriba.';$('mail-preview-accounts').replaceChildren();$('mail-preview-empty').hidden=true;
        renderDetails($('mail-preview-warnings'),warnings,failures);}
    }catch(err){invalidate();notice(resultSummary({deleted,mail:true,demo:host.demo,interrupted:true}),true);$('mail-preview-result').hidden=false;$('mail-preview-empty').hidden=true;$('mail-preview-accounts').replaceChildren();$('mail-message-total').textContent=String(deleted);$('mail-estimate').textContent=formatBytes(estimated);$('mail-preview-caption').textContent='Resultado parcial';$('mail-estimate-percent').textContent='Solo se cuentan las eliminaciones confirmadas.';renderDetails($('mail-preview-warnings'),[...warnings,err.message],failures);}
    finally{host.setBusy(false);host.activity.end(ticket);}
  }
  $('mail-preset').addEventListener('change',updateDate);$('mail-date').addEventListener('change',updateDate);
  for(const input of document.querySelectorAll('input[name="mail-scope"]'))input.addEventListener('change',()=>{invalidate();controls();});
  $('mail-account-search').addEventListener('input',renderAccounts);
  $('mail-select-visible').addEventListener('click',()=>{const rows=shownAccounts(),all=rows.every(a=>state.selected.has(a.address));for(const row of rows)all?state.selected.delete(row.address):state.selected.add(row.address);invalidate();renderAccounts();controls();});
  $('mail-reload').addEventListener('click',loadAccounts);$('mail-preview').addEventListener('click',preview);$('mail-review').addEventListener('click',review);
  for(const button of document.querySelectorAll('.mail-close'))button.addEventListener('click',()=>$('mail-confirmation').close());
  for(const id of ['mail-irreversible-ack','mail-all-ack'])$(id).addEventListener('change',validateConfirm);
  $('mail-confirm-word').addEventListener('input',validateConfirm);$('mail-confirm-form').addEventListener('submit',remove);
  updateDate();loadAccounts();
  return {setBusy(value){state.busy=value;renderAccounts();controls();},invalidate};
}
