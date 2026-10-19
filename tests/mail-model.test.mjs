import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {presetDate,cutoffFromDate,cutoffLabel,canConfirmMail} from '../plugin/mail-model.mjs';
test('one month is a calendar month, clamped at month end',()=>{
  assert.equal(presetDate('month',new Date(2026,2,31)),'2026-02-28');
  assert.equal(presetDate('month',new Date(2024,2,31)),'2024-02-29');
  assert.equal(presetDate('month',new Date(2026,0,31)),'2025-12-31');
});
test('one year handles leap day and resolves an explicit cutoff',()=>{
  assert.equal(presetDate('year',new Date(2024,1,29)),'2023-02-28');
  assert.equal(presetDate('year',new Date(2026,9,19)),'2025-10-19');
});
test('invalid dates and future cutoffs are rejected',()=>{
  for(const value of ['2026-02-30','2100-01-01','2026-10-19 OR ALL',''])assert.throws(()=>cutoffFromDate(value,new Date(2026,9,19)));
});
test('date payload fixes midnight and offset for preview and deletion',()=>{
  const date=cutoffFromDate('2026-09-19',new Date(2026,9,19));
  assert.equal(date.date,'2026-09-19');assert.equal(date.utc_offset_minutes,-new Date(2026,8,19).getTimezoneOffset());
  assert.equal(cutoffLabel({date:'2026-09-19',utc_offset_minutes:-180}),'19/09/2026 a las 00:00 (UTC−03:00)');
});
test('permanent mail deletion needs exact phrase and irreversible acknowledgement',()=>{
  const value={word:'ELIMINAR CORREOS',irreversible:true,allFolders:false,allAck:false};
  assert.equal(canConfirmMail(value),true);
  for(const change of [{word:'ELIMINAR'},{word:'eliminar correos'},{word:'ELIMINAR CORREOS '},{irreversible:false},{busy:true}])assert.equal(canConfirmMail({...value,...change}),false);
});
test('all folders requires its separate acknowledgement',()=>{
  const value={word:'ELIMINAR CORREOS',irreversible:true,allFolders:true,allAck:false};
  assert.equal(canConfirmMail(value),false);assert.equal(canConfirmMail({...value,allAck:true}),true);
});
test('Santiago daylight saving preserves the actual start of a day without midnight',()=>{
  const source="import {cutoffFromDate} from './plugin/mail-model.mjs'; console.log(JSON.stringify(cutoffFromDate('2026-09-06',new Date(2026,9,19))))";
  const cutoff=JSON.parse(execFileSync(process.execPath,['--input-type=module','-e',source],{env:{...process.env,TZ:'America/Santiago'},encoding:'utf8'}));
  assert.equal(cutoff.start_hour,1);assert.equal(cutoff.utc_offset_minutes,-180);
  assert.equal(cutoffLabel(cutoff),'06/09/2026 a las 01:00 (UTC−03:00)');
});
