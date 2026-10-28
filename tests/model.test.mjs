import test from 'node:test';
import assert from 'node:assert/strict';
import {totals,filterItems,formatBytes,formatPercent,candidateRows,storageSegments} from '../plugin/model.js';
const items=[{id:'a',name:'old.log',path:'/home/u/logs/old.log',category:'logs',modified_at:100,size_bytes:80,reclaimable_bytes:60,selectable:true},{id:'b',name:'backup.zip',path:'/home/u/backups/backup.zip',category:'backups',modified_at:200,size_bytes:90,reclaimable_bytes:90,selectable:false}];
test('selection immediately updates recovered percentages and projected free space',()=>{const t=totals(items,new Set(['a']),{used_bytes:400,limit_bytes:1000});assert.equal(t.bytes,60);assert.equal(t.quotaPercent,6);assert.equal(t.usedPercent,15);assert.equal(t.projectedFree,660);});
test('protected rows cannot enter totals, including manipulated selection',()=>{assert.equal(totals(items,new Set(['b']),{used_bytes:400,limit_bytes:1000}).bytes,0);});
test('unlimited and unavailable quota never invent a percentage',()=>{const t=totals(items,new Set(['a']),{used_bytes:400,limit_bytes:null});assert.equal(t.quotaPercent,null);assert.equal(t.usedPercent,15);assert.equal(t.projectedFree,null);assert.equal(formatPercent(null),'—');});
test('over-quota account must recover overage before free quota appears',()=>{assert.equal(totals(items,new Set(['a']),{used_bytes:1200,limit_bytes:1000}).projectedFree,0);});
test('zero usage does not divide by zero',()=>{assert.equal(totals(items,new Set(['a']),{used_bytes:0,limit_bytes:1000}).usedPercent,null);});
test('filters only change view, selected totals persist',()=>{assert.equal(filterItems(items,{category:'backups'}).length,1);assert.equal(totals(items,new Set(['a']),{used_bytes:400,limit_bytes:1000}).bytes,60);});
test('search includes full location and category/date filters combine',()=>{assert.equal(filterItems(items,{search:'/backups/'}).length,1);assert.equal(filterItems(items,{category:'logs',age:7,now:8*86400}).length,1);});
test('sparse allocation drives estimate, not logical file size',()=>{assert.equal(totals(items,new Set(['a']),{}).bytes,60);});
test('binary units and locale are explicit',()=>{assert.equal(formatBytes(1024**3),'1 GiB');assert.equal(formatBytes(null),'—');assert.equal(formatBytes(0),'0 B');});
test('storage colors reconcile unclassified usage and available quota',()=>{
  const report=storageSegments({used_bytes:600,limit_bytes:1000},{status:'ok',basis:'allocated_bytes',categories:[{key:'mail',bytes:200},{key:'web',bytes:300},{key:'other',bytes:50}]});
  assert.equal(report.segments.find(row=>row.key==='other').bytes,100);
  assert.equal(report.segments.find(row=>row.key==='free').bytes,400);
  assert.equal(report.segments.reduce((n,row)=>n+row.width,0),100);
  assert.equal(report.discrepancy,false);
});
test('inconsistent categories are hidden and never replace the confirmed quota',()=>{
  const report=storageSegments({used_bytes:2e6,limit_bytes:5e6},{status:'ok',basis:'allocated_bytes',categories:[{key:'mail',bytes:2e6},{key:'databases',bytes:2e6}]});
  assert.equal(report.total,2e6);assert.equal(report.inconsistent,true);assert.equal(report.known,false);
  assert.equal(report.segments.find(row=>row.key==='databases'),undefined);
  assert.equal(report.segments.find(row=>row.key==='free').bytes,3e6);
  assert.equal(report.segments.reduce((n,row)=>n+row.width,0),100);
});
test('133 GiB of virtual mail cannot displace the real free space of an 80 GiB plan',()=>{
  const gib=1024**3;
  const report=storageSegments({used_bytes:76.6*gib,limit_bytes:80*gib},{status:'ok',basis:'allocated_bytes',categories:[{key:'mail',bytes:133.43*gib}]});
  assert.equal(report.known,false);assert.equal(report.total,76.6*gib);
  assert.equal(report.segments.find(row=>row.key==='mail'),undefined);
  assert.ok(Math.abs(report.segments.find(row=>row.key==='free').bytes-3.4*gib)<1);
});
test('mailbox sizes without a physical measurement marker cannot become account categories',()=>{
  const report=storageSegments({used_bytes:600,limit_bytes:1000},{status:'ok',categories:[{key:'mail',bytes:300}]});
  assert.equal(report.known,false);assert.deepEqual(report.segments.map(row=>row.key),['unknown','free']);
});
test('small quota rounding differences adjust only the unclassified remainder',()=>{
  const report=storageSegments({used_bytes:1000,limit_bytes:2000},{status:'ok',basis:'allocated_bytes',categories:[{key:'mail',bytes:800},{key:'other',bytes:205}]});
  assert.equal(report.known,true);assert.equal(report.segments.find(row=>row.key==='mail').bytes,800);
  assert.equal(report.segments.find(row=>row.key==='other').bytes,200);
  assert.equal(report.total,1000);
});
test('unavailable breakdown stays neutral and does not invent empty categories',()=>{
  const report=storageSegments({used_bytes:600,limit_bytes:1000},{status:'unavailable'});
  assert.equal(report.known,false);assert.deepEqual(report.segments.map(row=>row.key),['unknown','free']);
  assert.equal(report.segments[0].bytes,600);
  assert.equal(storageSegments({},{}).segments.length,0);
});
test('storage bar remains bounded for unlimited and over-quota accounts',()=>{
  const storage={status:'ok',basis:'allocated_bytes',categories:[{key:'web',bytes:1200}]};
  for(const quota of [{used_bytes:1200,limit_bytes:null},{used_bytes:1200,limit_bytes:1000}]){
    const report=storageSegments(quota,storage);
    assert.equal(report.segments.reduce((n,row)=>n+row.width,0),100);
    assert.equal(report.segments.find(row=>row.key==='free')?.bytes||0,0);
  }
});
test('invalid or duplicate storage categories fall back to total usage',()=>{
  for(const categories of [[{key:'mail',bytes:-1}],[{key:'mail',bytes:1},{key:'mail',bytes:1}],[{key:'unsupported',bytes:100}]]){
    assert.equal(storageSegments({used_bytes:10},{status:'ok',basis:'allocated_bytes',categories}).known,false);
  }
});

const message=(id,account,category='spam',modified_at=100)=>({id,account,category,modified_at,name:id,path:`/home/u/mail/example.com/${account.split('@')[0]}/.${category==='spam'?'Junk':'Trash'}/cur/${id}`,selectable:true,risk:'review',size_bytes:100,reclaimable_bytes:80});
test('only eligible rows are shown, while protected items remain excluded from totals',()=>{
  assert.deepEqual(candidateRows(items).map(row=>row.id),['a']);
  assert.equal(totals(items,new Set(['b']),{}).bytes,0);
});
test('spam and trash group by account and category without losing exact deletion identities',()=>{
  const source=[message('1','a@example.com'),message('2','a@example.com'),message('3','b@example.com'),message('4','a@example.com','mail_trash')];
  const rows=candidateRows(source);
  assert.equal(rows.length,3);
  const group=rows.find(row=>row.members.length===2);
  assert.equal(group.name,'Spam · a@example.com');assert.equal(group.reclaimable_bytes,160);
  assert.deepEqual(group.members.map(item=>item.id),['1','2']);
  assert.equal(group.path,'/home/u/mail/example.com/a/.Junk/cur/');
  const picked=new Set(group.members.map(item=>item.id));
  assert.equal(totals(source,picked,{}).bytes,160);assert.equal(totals(source,picked,{}).picked.length,2);
});
test('filters apply to individual members before grouping and never include hidden or protected messages',()=>{
  const source=[message('old','a@example.com'),message('recent','a@example.com','spam',8*86400),{...message('protected','a@example.com'),selectable:false}];
  const rows=candidateRows(source,{age:7,now:9*86400});
  assert.equal(rows.length,1);assert.deepEqual(rows[0].members.map(item=>item.id),['old']);
  assert.equal(rows[0].reclaimable_bytes,80);
  assert.deepEqual(candidateRows(source,{search:'recent'})[0].members.map(item=>item.id),['recent']);
  const trash=source.slice(0,2).map(item=>({...item,account:undefined,category:'trash',path:'/home/u/.trash/'+item.id}));
  assert.equal(candidateRows(trash)[0].members.length,2);
});
