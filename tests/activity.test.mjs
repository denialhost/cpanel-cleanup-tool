import test from 'node:test';
import assert from 'node:assert/strict';
import {progressState,groupFailures,resultSummary} from '../plugin/activity.js';

test('unknown scan size never invents a percentage',()=>{
  for(const total of [undefined,0,NaN,Infinity]) assert.equal(progressState(20,total).percent,null);
});
test('percentage counts completed attempts including failed elements',()=>{
  assert.equal(progressState(0,727).percent,0);
  assert.equal(progressState(250,727).percent,34);
  assert.equal(progressState(500,727).percent,68);
  assert.equal(progressState(727,727).percent,100);
  assert.equal(progressState(999,727).percent,100);
});
test('hundreds of identical failures become one group with bounded examples',()=>{
  const failures=Array.from({length:727},(_,i)=>({path:`mail/demo/${i}`,error:'El elemento cambió.'}));
  const groups=groupFailures(failures);
  assert.equal(groups.length,1); assert.equal(groups[0].count,727); assert.equal(groups[0].examples.length,3);
  const summary=resultSummary({failed:727});
  assert.match(summary,/No se eliminó/); assert.match(summary,/727 pendientes/); assert.ok(summary.length<150);
});
test('mixed results and interrupted requests do not claim all items were deleted',()=>{
  assert.match(resultSummary({deleted:250,bytes:1024,failed:477}),/250 elementos eliminados.*477 pendientes/);
  const interrupted=resultSummary({deleted:250,interrupted:true});
  assert.match(interrupted,/250 eliminaciones confirmadas/); assert.match(interrupted,/Vuelve a analizar/);
});
