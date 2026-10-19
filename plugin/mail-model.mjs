export function localDateString(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
}
export function presetDate(preset, now = new Date()) {
  let year=now.getFullYear(), month=now.getMonth();
  if(preset==='month'){month--;if(month<0){month=11;year--;}}
  else if(preset==='year')year--;
  else throw new Error('Selecciona un período válido.');
  const day=Math.min(now.getDate(),new Date(year,month+1,0).getDate());
  return localDateString(new Date(year,month,day));
}
export function cutoffFromDate(value, now = new Date()) {
  if(!/^\d{4}-\d{2}-\d{2}$/.test(value))throw new Error('Selecciona una fecha válida.');
  const [year,month,day]=value.split('-').map(Number);
  const local=new Date(year,month-1,day);
  if(year<1970 || localDateString(local)!==value || local>now)throw new Error('La fecha debe ser válida y no estar en el futuro.');
  // Freeze the first representable time if a daylight-saving change skips midnight.
  return {date:value,utc_offset_minutes:-local.getTimezoneOffset(),start_hour:local.getHours(),start_minute:local.getMinutes()};
}
export function cutoffLabel(cutoff) {
  const [year,month,day]=cutoff.date.split('-');
  const offset=cutoff.utc_offset_minutes;
  const sign=offset<0?'−':'+';
  const hours=String(Math.floor(Math.abs(offset)/60)).padStart(2,'0');
  const minutes=String(Math.abs(offset)%60).padStart(2,'0');
  const time=`${String(cutoff.start_hour||0).padStart(2,'0')}:${String(cutoff.start_minute||0).padStart(2,'0')}`;
  return `${day}/${month}/${year} a las ${time} (UTC${sign}${hours}:${minutes})`;
}
export function canConfirmMail({word,irreversible,allFolders,allAck,busy=false}) {
  return !busy && word==='ELIMINAR CORREOS' && irreversible===true && (!allFolders || allAck===true);
}
