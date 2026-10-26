// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
'use strict';
const test=require('node:test'), assert=require('node:assert/strict');
const fs=require('fs'), path=require('path'), vm=require('vm');
const F=require('./fill_env.js');
const {setup,status,SRC,BODY}=require('./check_env.js');

const row=r=>r.items[0]||{};

test('check-answer: select writes, saves and checks until correct (16)',async()=>{
  const e=setup();const r=await e.run();assert.equal(r.fatal,undefined,JSON.stringify(r));assert.deepEqual(e.presses,['0','1','2']);assert.equal(row(r).stop,'correct');assert.equal(row(r).state,'2');assert.equal(e.requests.length,2);assert.ok(e.requests.every(r=>r.t1!==null));
});

const refusals=[
 ['disabled (1)',e=>{e.cfg.enabled=false;},'check-answer-off',true],
 ['capture single (2)',e=>{e.job.attempts='single';},'single-attempt'],
 ['live single (3)',e=>{e.content.singleAttempt=true;},'single-attempt'],
 ['missing attempts (4)',e=>{delete e.content.singleAttempt;},'attempts-unknown'],
 ['null attempts (4)',e=>{e.content.singleAttempt=null;},'attempts-unknown'],
 ...['noAssessment','graded',undefined].map(v=>['assessment '+v+' (5)',e=>{e.content.assessmentType=v;},'assessment-live']),
 ['locked (6)',e=>{e.input.attributes.locked=true;},'locked'],
 ['later readonly (6)',e=>{e.content.isReadOnly=true;},'read-only'],
 ['unknown readonly (6)',e=>{delete e.raw.attributes.panelState.isReadOnly;},'read-only-unknown'],
 ['commit control (7)',e=>{e.node('button',{parent:e.panel,text:'Submit'});},'commit-control-present'],
 ['no button (8)',e=>{e.button.open=false;},'no-button'],
 ['two buttons (8)',e=>{const b=e.node('button',{parent:e.panel,text:'Check Answer'});e.contexts.set(b,{$data:e.raw});},'several-buttons'],
 ['disabled button (8)',e=>{e.button.disabled=true;},'button-disabled'],
 ['other panel (9)',e=>{e.contexts.set(e.button,{$data:{attributes:{panelKey:'OTHER'}}});},'no-button'],
 ['edited since verify (10)',e=>{e.input.attributes.selectedAnswers=[2];},'changed-since-verify'],
 ...['text-response','table-completion','image-annotation'].map(t=>['unsupported '+t+' (11)',e=>{e.job.type=t;},'type-not-checkable']),
 ['not painted (12)',e=>{e.pages[0].current=F.obs(false);},'page-not-displayed'],
 ['option order (13)',e=>{e.content.answers.models.reverse();},'shape-changed'],
 ['visible dialog (14)',e=>{e.dialog('Correct');},'dialog-present',true],
 ['old guard (15)',e=>{const old={version:1};Object.defineProperty(e.ctx,'LtGuard',{value:old});},'guard-version',true],
 ['stale calibration (15)',e=>{e.cfg.save_wait.calibration.client_sig='old';},'calibration-stale',true],
];
for(const [name,edit,reason,call] of refusals) {
 if(name.startsWith('old guard'))continue; // The non-configurable v1 property is tested at the guard seam.
 test('check-answer: refuses '+name,async()=>{const e=setup();edit(e);const r=await e.run();assert.equal(call?r.fatal:row(r).stop,reason,JSON.stringify(r));assert.equal(e.presses.length,0);assert.equal(e.requests.length,0);});
}

test('check-answer: all options wrong restores draft without another press (17)',async()=>{const e=setup({correctAt:99});const r=await e.run();assert.equal(row(r).stop,'options-exhausted');assert.equal(row(r).state,'0');assert.deepEqual(e.presses,['0','1','2']);assert.equal(e.requests.length,3);});
test('check-answer: no cap with twelve options (29)',async()=>{const e=setup({pool:Array.from({length:12},(_,i)=>'Option '+i),correctAt:12});const r=await e.run();assert.equal(e.presses.length,12);assert.equal(row(r).stop,'correct');});
for(const [name,response,stop] of [
 ['unread (30,32)',()=>null,'verdict-unread'],
 ['late verdict (34)',()=>({verdict:'Correct',delay:11000}),'verdict-unread'],
 ['refused dialog (35)',()=>({verdict:'Submit; cannot be undone'}),'dialog-refused'],
 ['model answer',()=>({verdict:'Model answer'}),'verdict-unread'],
 ['foreign verdict',()=>({verdict:'Richtig'}),'verdict-unread'],
 ['native confirm (37)',()=>({verdict:'Incorrect',confirm:true}),'guard-trip'],
 ['locked after press (38)',()=>({verdict:'Incorrect',lock:true}),'locked'],
 ['Lt changes state (21,31)',()=>({verdict:'Incorrect',change:[2]}),'lt-changed-state'],
 ['dialog stuck',()=>({verdict:'Incorrect',stuck:true}),'dialog-stuck'],
 ['extra dialog button',()=>({verdict:'Correct',extra:'Cancel'}),'verdict-unread']]) {
 test('check-answer: '+name,async()=>{const e=setup({verdict:response});const r=await e.run([e.job,{...e.job,item:'p1-OTHER'}]);assert.equal(row(r).stop,stop,JSON.stringify(r));assert.equal(e.presses.length,1);assert.equal(e.requests.length,0);if(['dialog-refused','verdict-unread'].includes(stop))assert.equal(e.closes,0);});
}
test('check-answer: synchronous dialog observed and compact receipt (33,42)',async()=>{const e=setup({verdict:()=>({verdict:'Correct',feedback:'Z'.repeat(400)})});const r=await e.run();assert.equal(row(r).stop,'correct');assert.equal(row(r).feedback.length,300);assert.ok(F.maxDepth(r,0)<=4);assert.equal(r.policy.enabled,true);assert.ok(row(r).presses.every(p=>typeof p==='string'));assert.ok(!JSON.stringify(r).includes('Option'));});
test('check-answer: two dialogs remain untouched (36)',async()=>{const e=setup({verdict:env=>{env.dialog('Correct');return {verdict:'Correct'};}});const r=await e.run();assert.equal(r.fatal,'verdict-unread');assert.equal(e.closes,0);});
test('check-answer: multiselect without marks checks once (24)',async()=>{const e=setup({multi:true});const r=await e.run();assert.equal(row(r).stop,'no-part-marks');assert.equal(e.presses.length,1);});
test('check-answer: multiselect strict subset is pressed alone (25)',async()=>{const e=setup({multi:true,state:[0,1],verdict:(_,n)=>n===1?{verdict:'Incorrect',marks:{right:[0],wrong:[1]}}:{verdict:'Correct',marks:{right:[0]}}});const r=await e.run();assert.equal(row(r).stop,'correct');assert.deepEqual(e.presses,['0,1','0']);});
test('check-answer: multiselect grows after right-set exception (66)',async()=>{const e=setup({multi:true,state:[0,1],verdict:(_,n)=>n===1?{verdict:'Incorrect',marks:{right:[0],wrong:[1]}}:n===2?{verdict:'Partially correct',marks:{right:[0]}}:{verdict:'Correct'}});const r=await e.run();assert.equal(row(r).stop,'correct');assert.deepEqual(e.presses,['0,1','0','0,2']);});
for(const kind of ['labels','categories']) {
 test('check-answer: '+kind+' moves wrong parts with model paths (18,19,23)',async()=>{
  const e=setup({kind,verdict:(_,n)=>n===1?{verdict:'Incorrect',marks:{right:[0],wrong:[1,2]}}:{verdict:'Correct',marks:{right:[0,1,2]}}});const r=await e.run();assert.equal(row(r).stop,'correct',JSON.stringify(r));assert.ok(e.input.calls.every(([li])=>li>=0));assert.ok(e.presses.every(s=>s.includes('0:0')));
 });
 test('check-answer: '+kind+' append semantics refuse read-back (write-mismatch)',async()=>{const e=setup({kind,move:false,verdict:()=>({verdict:'Incorrect',marks:{right:[0],wrong:[1,2]}})});const r=await e.run();assert.equal(row(r).stop,'write-mismatch',JSON.stringify(r));assert.equal(e.presses.length,1);});
}
test('check-answer: labels no wrong mark stop without writes (20)',async()=>{const e=setup({kind:'labels',verdict:()=>({verdict:'Incorrect'})});const r=await e.run();assert.equal(row(r).stop,'no-progress');assert.equal(r.writes,0);});
test('check-answer: identical label swap changes only index hash (22)',async()=>{const e=setup({kind:'labels',pool:['Same','Same'],state:[[0,0],[1,1]],verdict:(_,n)=>n===1?{verdict:'Incorrect',marks:{wrong:[0,1]}}:{verdict:'Correct'}});const r=await e.run();assert.equal(row(r).stop,'correct',JSON.stringify(r));const a=row(r).draft_h.split('/'),b=row(r).final_h.split('/');assert.equal(a[0],b[0]);assert.notEqual(a[1],b[1]);assert.equal(row(r).moved,true);});
for(const signal of ['fallback','unconfirmed','error'])test('check-answer: save '+signal+' ends call (27,28)',async()=>{const e=setup({signal});const r=await e.run();assert.equal(r.fatal,'save-'+signal);assert.equal(e.presses.length,1);});
test('check-answer: edit during save refuses next press (26)',async()=>{const e=setup();e.after(50,()=>{e.input.attributes.selectedAnswers=[2];});const r=await e.run();assert.equal(row(r).stop,'changed-since-last-write');assert.equal(e.presses.length,1);});
test('check-answer: DENY list matches confirmation helper (39)',()=>{const e=setup();const expected=fs.readFileSync(path.join(F.BROWSER,'confirm_dialog.js'),'utf8').match(/const LT_DIALOG_DENY = (\[[\s\S]*?\]);/)[1];const other=vm.runInNewContext(expected);assert.deepEqual(Array.from(e.ctx.LtCheck.DENY,String),Array.from(other,String));});
test('check-answer: source limits effects to press and dialog close (43)',()=>{assert.equal((SRC.match(/\.click\(/g)||[]).length,2);assert.ok(!/dispatchEvent|\.form\b|\.submit\(|\.commit\(/.test(SRC));});

test('check-answer: deadline leaves saved progress and unreached items for resume (40,41,67)',async()=>{
 const options=Array.from({length:12},(_,i)=>'Option '+i);
 const e=setup({pool:options,verdict:(_,n)=>({verdict:n===12?'Correct':'Incorrect',delay:9000})});
 const first=await e.run([e.job,{...e.job,item:'p1-later'}]);
 assert.equal(row(first).stop,'not-finished');assert.equal(first.items[1].stop,'not-finished');assert.ok(e.clock.now()-1000000<90000);
 const prior=JSON.parse(JSON.stringify(row(first)));e.job.resume={...prior,presses_before:prior.presses_before+prior.presses.length};e.cfg.resumed=true;
 const second=await e.run();assert.ok(row(second).presses.length>0,JSON.stringify(second));assert.ok(!row(second).presses.some(p=>prior.tried.includes(p.split('|')[0])));
 assert.equal(row(second).presses_before,prior.presses.length);
});

for(const kind of ['select','multi','labels','drag','categories'])test('check-answer: 500 seeded finite-key runs for '+kind+' (64)',async()=>{
 for(let seed=1;seed<=500;seed++) {
  const rand=F.prng(seed),n=2+Math.floor(rand()*4),pool=Array.from({length:n},(_,i)=>'Part '+i);
  const key=Array.from({length:n},()=>Math.floor(rand()*(kind==='categories'?3:n)));
  const selection=new Set(Array.from({length:n},(_,i)=>i).filter(()=>rand()<0.5));if(!selection.size)selection.add(0);
  const o={pool,kind:['select','multi'].includes(kind)?undefined:kind==='drag'?'labels':kind,multi:kind==='multi',bins:['X','Y','Z']};
  if(o.kind)o.state=Array.from({length:n},(_,i)=>[i,kind==='categories'?0:i]);else o.state=kind==='multi'?[...selection]:[0];
  const target=kind==='select'?Math.floor(rand()*n):selection;
  o.verdict=(e,num,snap)=>{
    const parts=e.ctx.LtCheck.decode(e.job.kind,snap.state);
    const correct=kind==='select'?parts[0]===target:kind==='multi'?parts.length===target.size&&parts.every(i=>target.has(i)):parts.every(([li,ti])=>key[li]===ti);
    const right=[],wrong=[];
    for(const p of parts){if(rand()<0.35)continue;const good=Array.isArray(p)?key[p[0]]===p[1]:target instanceof Set?target.has(p):p===target;
      (good?right:wrong).push(Array.isArray(p)?(kind==='categories'?p[0]:p[1]):p);}
    return {verdict:correct?'Correct':'Incorrect',marks:{right,wrong}};
  };
  const e=setup(o);if(kind==='drag')e.job.type='drag-and-drop-labeling';
  const r=await e.run(),item=row(r);assert.equal(r.fatal,undefined,kind+' seed '+seed+' '+JSON.stringify(r));
  assert.ok(['correct','options-exhausted','no-next-attempt','no-part-marks','no-progress','marks-unmapped'].includes(item.stop),kind+' seed '+seed+' '+item.stop);
  const limit=kind==='select'?n:kind==='multi'?2*n+1:kind==='categories'?n*3+1:n*n+1;
  assert.ok(item.presses.length<=limit);assert.equal(new Set(e.presses).size,e.presses.length);
  const decided=new Set(),bad=new Set(),rightSet=new Set();
  for(let i=0;i<item.presses.length;i++){
    const [state,verdict,rp,wp]=item.presses[i].split('|'),right=rp==='-'?[]:rp.split(','),wrong=wp==='-'?[]:wp.split(',');
    const values=state?state.split(','):[];assert.ok(values.every(p=>!bad.has(p)),kind+' retried wrong part seed '+seed);
    const added=right.concat(wrong).filter(p=>!decided.has(p));right.forEach(p=>rightSet.add(p));
    const exception=kind==='multi'&&values.length>0&&values.length===rightSet.size&&values.every(v=>rightSet.has(v));
    if(verdict!=='correct'&&i<item.presses.length-1&&kind!=='select')assert.ok(added.length||exception,kind+' no progress seed '+seed);
    right.concat(wrong).forEach(p=>decided.add(p));wrong.forEach(p=>bad.add(p));
  }
 }
});

test('check-answer: old guard refuses at call start (15,49)',async()=>{const e=setup({oldGuard:true});const r=await e.ctx.LtCheck.run(e.cfg,[e.job]);assert.equal(r.fatal,'guard-version');assert.equal(e.presses.length,0);});
for(const value of [undefined,null,1,'true'])test('check-answer: enabled must be literal true '+String(value)+' (1)',async()=>{const e=setup();e.cfg.enabled=value;const r=await e.run();assert.equal(r.fatal,'check-answer-off');assert.equal(e.presses.length,0);});
test('check-answer: repeated decided marks terminate and restore best (65)',async()=>{
 const e=setup({kind:'categories',verdict:(_,n)=>({verdict:'Incorrect',marks:n===1?{right:[0],wrong:[1,2]}:{right:[0]}})});
 const r=await e.run();assert.equal(row(r).stop,'no-progress');
 assert.equal(e.presses.length,2);assert.equal(row(r).state,e.job.draft_state);
});
test('check-answer: a previously unreached independent item resumes (67)',async()=>{
 const e=setup({pool:Array.from({length:12},(_,i)=>'Option '+i),verdict:()=>({verdict:'Incorrect',delay:9000})});
 const inp=e.model('L',{latency:100});inp.attributes.selectedAnswers=[0];
 const raw={attributes:{panelKey:'L',pluginKey:e.raw.attributes.pluginKey,panelState:{content:e.content,input:inp,isReadOnly:false}}};
 e.pages[0].allPanels=F.obs([e.raw,raw]);
 e.cfg.classification_pin.items.L={...e.cfg.classification_pin.items.K};
 const panel=e.node('div',{attrs:{'data-panel-key':'L'}});e.contexts.set(panel,{$data:raw});
 const b=e.node('button',{text:'Check Answer',attrs:{type:'button'},parent:panel,on:{click:()=>{e.presses.push('L:0');e.dialog('Correct');}}});e.contexts.set(b,{$data:raw});
 const other={...e.job,item:'p1-L',panel_key:'L'};
 const first=await e.run([e.job,other]);assert.equal(first.items[1].stop,'not-finished');assert.equal(first.items[1].presses.length,0);
 other.resume={...first.items[1],presses_before:0};e.cfg.resumed=true;
 const second=await e.run([other]);assert.equal(row(second).stop,'correct');assert.ok(e.presses.includes('L:0'));
});
test('check-answer: unread verdict poisons the document across script reload and resume',async()=>{
 // K's late Correct must never become evidence for L's pending Incorrect.
 function addLater(e) {
  const inp=e.model('L',{latency:100});inp.attributes.selectedAnswers=[0];
  const raw={attributes:{panelKey:'L',pluginKey:e.raw.attributes.pluginKey,panelState:{content:e.content,input:inp,isReadOnly:false}}};
  e.pages[0].allPanels=F.obs([e.raw,raw]);
 e.cfg.classification_pin.items.L={...e.cfg.classification_pin.items.K};
  const panel=e.node('div',{attrs:{'data-panel-key':'L'}});e.contexts.set(panel,{$data:raw});
  let count=0;
  const b=e.node('button',{text:'Check Answer',attrs:{type:'button'},parent:panel,on:{click:()=>{
   e.presses.push('L:'+inp.attributes.selectedAnswers.join(','));const n=++count;
   e.after(2000,()=>e.dialog(n===1?'Incorrect':'Correct'));
  }}});e.contexts.set(b,{$data:raw});
  return {...e.job,item:'p1-L',panel_key:'L'};
 }
 const e=setup({verdict:()=>({verdict:'Correct',delay:11000})}),other=addLater(e);
 const first=await e.run([e.job,other]);
 assert.equal(first.fatal,'verdict-unread');assert.equal(first.items[1].stop,'not-finished');
 other.resume={...first.items[1],presses_before:0};e.cfg.resumed=true;
 e.load(BODY,'check_answer.js');
 const second=await e.run([other]);
 assert.equal(second.fatal,'fresh-document-required');assert.deepEqual(e.presses,['0']);
 await e.clock.advance(2000);assert.equal(e.closes,0);assert.equal(e.requests.length,0);
 const fresh=setup(),remaining=addLater(fresh);
 fresh.cfg.resumed=true;fresh.cfg.save_wait.calibration.path_template='/api/panels/L/state';
 remaining.resume=other.resume;
 const resumed=await fresh.run([remaining]);
 assert.equal(resumed.fatal,undefined,JSON.stringify(resumed));
 assert.equal(row(resumed).stop,'correct');assert.deepEqual(fresh.presses,['L:0','L:1']);
});
test('check-answer: a resumed call with no reserve terminates remaining jobs (67)',async()=>{
 const e=setup();e.cfg.resumed=true;e.cfg.dialog_max_ms=90000;
 const r=await e.run();assert.equal(row(r).stop,'no-progress');assert.equal(e.presses.length,0);
});

test('check-answer: fill receipts preserve live label identity without capture order',async()=>{
 let inp;
 const e=F.makeEnv(env=>{const p=F.labelPanel(env,'K',['Z','A'],[[0.1,0.2],[0.3,0.4]],{});inp=p.inp;return F.fourPages(env,[p.p]);});
 const job={item:'p4-K',panel_key:'K',plugin_key:'kcImageLabelling',kind:'labels',placements:[{label:'A',loc:0},{label:'Z',loc:1}],h:F.L0.djb2(F.L0.canon.labels([[0,'A'],[1,'Z']]))};
 const r=await F.run(e,{},[job],{probe:async()=>({ok:true,page_index:4,panels:[]})});
 const filled=r.items[0];assert.equal(filled.result,'written',JSON.stringify(r));
 assert.deepEqual(Array.from(filled.pool_h),['Z','A'].map(F.L0.djb2));
 assert.equal(filled.index_state,'0:1,1:0');assert.deepEqual(inp.calls,[[1,0],[0,1]]);
});

test('check-answer: earlier submitted singles must remain read-only',async()=>{
 const e=setup();e.cfg.single_attempt_locked=[[1,'K']];
 const r=await e.run();assert.equal(r.fatal,'single-attempt-not-locked');assert.equal(e.presses.length,0);assert.equal(e.requests.length,0);
});

for(const [name,response,stop] of [
 ['unread last option',()=>null,'verdict-unread'],
 ['Lt changes the last option',()=>({verdict:'Incorrect',change:[]}),'lt-changed-state']])test('check-answer: '+name+' never restores (30,31)',async()=>{
 const e=setup({pool:['Only'],verdict:response});const r=await e.run();
 assert.equal(row(r).stop,stop);assert.equal(e.presses.length,1);assert.equal(e.requests.length,0);
});

test('check-answer: live classification text changes refuse every press',async()=>{
 const e=setup({correctAt:1});
 const stimulus=F.mdl({panelKey:'T',pluginKey:'kcTextArea',panelState:{content:{theText:'Ordinary exercise'}}});
 e.pages[0].allPanels=F.obs([stimulus,e.raw]);
 e.cfg.classification_pin={title:'Page 1',report:false,checkpoint:false,
  content:{T:{plugin_key:'kcTextArea',text:'Ordinary exercise'}},
  items:{K:{plugin_key:'select',attempts:'multi',assessment_type:'instantFeedback'}}};
 stimulus.attributes.panelState.content.theText='tRAT: Team readiness test';
 const r=await e.run();
 assert.equal(e.presses.length,0);
 assert.equal(row(r).stop,'classification-changed');
 assert.equal(r.fatal,'classification-changed');
 assert.equal(e.requests.length,0);
});
test('check-answer: classification changes between retries prevent another press or write',async()=>{
 const e=setup({verdict:env=>{env.pages[0].title=F.obs('Changed title');return {verdict:'Incorrect'};}});
 e.cfg.classification_pin={title:'Page 1',report:false,checkpoint:false,content:{},
  items:{K:{plugin_key:'select',attempts:'multi',assessment_type:'instantFeedback'}}};
 const r=await e.run();
 assert.equal(e.presses.length,1);
 assert.equal(row(r).stop,'classification-changed');
 assert.equal(r.fatal,'classification-changed');
 assert.equal(e.requests.length,0);
});

test('check-answer: missing classification pins require recapture',async()=>{
 const e=setup({correctAt:1});delete e.cfg.classification_pin;
 const r=await e.run();assert.equal(e.presses.length,0);assert.equal(r.fatal,'classification-changed');
});
test('check-answer: changed classification cannot resume in the same document',async()=>{
 const e=setup({correctAt:1});e.pages[0].title=F.obs('Changed title');
 const first=await e.run();assert.equal(first.fatal,'classification-changed');
 e.pages[0].title=F.obs('Page 1');
 const second=await e.run();assert.equal(second.fatal,'fresh-document-required');
 assert.equal(e.presses.length,0);assert.equal(e.requests.length,0);
});
test('check-answer: new author panels and page-wide attempt changes refuse checks',async()=>{
 for(const edit of [
  e=>{e.pages[0].allPanels=F.obs([e.raw,F.mdl({panelKey:'T',pluginKey:'kcTextArea',panelState:{content:{theText:'Team examination'}}})]);},
  e=>{e.pages[0].allPanels=F.obs([e.raw,F.mdl({panelKey:'__proto__',pluginKey:'kcTextArea',panelState:{content:{theText:'Team examination'}}})]);},
  e=>{e.pages[0].allPanels=F.obs([e.raw,F.mdl({panelKey:'L',pluginKey:'select',panelState:{content:{singleAttempt:true,assessmentType:'graded'},input:{}}})]);},
 ]) {
  const e=setup({correctAt:1});edit(e);const r=await e.run();
  assert.equal(e.presses.length,0);assert.equal(e.requests.length,0);assert.equal(r.fatal,'classification-changed');
 }
});
test('check-answer: unchanged authored text pins permit checks',async()=>{
 const e=setup({correctAt:1});
 e.pages[0].allPanels=F.obs([F.mdl({panelKey:'T',pluginKey:'kcTextArea',panelState:{content:{theText:'Ordinary exercise'}}}),e.raw]);
 e.cfg.classification_pin.content.T={plugin_key:'kcTextArea',text:'Ordinary exercise'};
 const r=await e.run();assert.equal(row(r).stop,'correct');assert.equal(e.presses.length,1);
});

test('check-answer: captured prose, captions, tables and popups stay checkable',async()=>{
 const {DOMParser}=require('../capture/inert_dom.js');
 const cases=[
  ['kcTextArea',{theText:'<p>Ordinary</p><ul><li>Exercise</li></ul>'},'Ordinary - Exercise'],
  ['kcTextArea',{theText:'<p>Ordinary <span hidden>tRAT</span>exercise</p>'},'Ordinary exercise'],
  ['image',{caption:'<p>Figure one</p>'},'Figure one'],
  ['table',{rows:F.coll([F.mdl({cells:F.coll([F.mdl({content:'<b>Observation</b>'})])})])},'Observation'],
  ['hotspot',{hotspots:F.coll([F.mdl({labelTitle:'Case',labelDescription:'Ordinary <i>finding</i>'})])},'Case: Ordinary finding'],
 ];
 for(const [plugin,content,expected] of cases) {
  const e=setup({correctAt:1});e.ctx.DOMParser=DOMParser;
  e.pages[0].allPanels=F.obs([F.mdl({panelKey:'T',pluginKey:plugin,panelState:{content}}),e.raw]);
  e.cfg.classification_pin.content.T={plugin_key:plugin,text:expected};
  const r=await e.run();assert.equal(row(r).stop,'correct',JSON.stringify(r));assert.equal(e.presses.length,1);
 }
});
