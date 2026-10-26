// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
'use strict';
const fs=require('fs'), path=require('path'), vm=require('vm');
const F=require('./fill_env.js'), H=F.harness();
const SRC=fs.readFileSync(path.join(F.BROWSER,'check_answer.js'),'utf8');
const BODY='(function () {\nconst LtFillLib = globalThis.LtFillLib;\n' + SRC.split('return await LtCheck.run(__LT_ARGS__.cfg, __LT_ARGS__.jobs);')[0] + '\nglobalThis.LtCheck = LtCheck;})();';
const status={candidate:false,rat:false,kind:null,sources:[],decision:'not-rat',check_allowed:true};

// The double changes only DOM and transport. The real guard, writer, network watcher and loop run.
function setup(o={}) {
  const page=H.page({title:'Page 1',current:true});
  const env=H.makeEnv({pages:[page],scripts:['https://lt.example.invalid/client.js']});
  env.clk=env.clock;env.moveMode=o.move!==false;env.spy={checkAnswer:0};env.save=()=>{};
  let raw, input;
  const labels=o.pool||['A','B','C'];
  if(o.kind==='labels') {const p=F.labelPanel(env,'K',labels,labels.map((_,i)=>[i/3,i/3]),{links:(o.state||[[0,0],[1,1],[2,2]]).map(([li,lo])=>F.mdl({labelIndex:li,locationIndex:lo}))});raw=p.p;input=p.inp;}
  else if(o.kind==='categories') {const p=F.catPanel(env,'K',labels,o.bins||['X','Y'],{});raw=p.p;input=p.inp;for(const [li,ci] of o.state||[[0,0],[1,0],[2,0]]) input.entries[ci].attributes.labelAnswerItems.models.push(F.mdl({labelIndex:li}));}
  else {input=env.model('K',{latency:100});input.attributes.selectedAnswers=o.state||[0];raw={attributes:{panelKey:'K',pluginKey:'select',panelState:{content:{answers:F.coll(labels.map(t=>F.mdl({answer:t})))},input}}};}
  const state=raw.attributes.panelState,c=state.content;
  Object.assign(c,{singleAttempt:false,assessmentType:'instantFeedback',multiselect:!!o.multi});
  state.isReadOnly=false;
  page.allPanels=F.obs([raw]);
  const contexts=new Map(),nodes=[];
  const original=env.ctx.ko.contextFor;
  env.ctx.ko.contextFor=el=>contexts.has(el)?contexts.get(el):original(el);
  const watchers=new Set();
  const mutate=()=>{Promise.resolve().then(()=>{for(const fn of [...watchers])fn();});};
  env.ctx.MutationObserver=class {constructor(fn){this.fn=fn;}observe(){watchers.add(this.fn);}disconnect(){watchers.delete(this.fn);}};
  const node=(tag,options={})=>{
    const el=env.el(tag,options);el.open=true;el.className=options.className||'';
    el.getClientRects=()=>el.open?[{}]:[];el.style={display:'block',visibility:'visible',opacity:'1'};
    el.querySelectorAll=sel=>nodes.filter(n=>n!==el&&inside(n,el)&&matches(n,sel));
    nodes.push(el);return el;
  };
  function inside(el,parent){for(let p=el.parentNode;p;p=p.parentNode)if(p===parent)return true;return false;}
  function matches(n,sel){if(sel==='*')return true;if(sel==='button')return n.tagName==='BUTTON';if(sel==='[role=dialog]')return n.getAttribute('role')==='dialog';if(sel==='[data-panel-key], .panel')return !!n.getAttribute('data-panel-key');return sel.split(',').some(s=>n.className.split(/\s+/).includes(s.trim().replace(/^\./,'')));}
  env.ctx.getComputedStyle=el=>el.style;
  const q0=env.ctx.document.querySelector;
  env.ctx.document.querySelector=s=>q0(s)||nodes.find(n=>matches(n,s));
  env.ctx.document.querySelectorAll=s=>nodes.filter(n=>matches(n,s));
  const panel=node('div',{attrs:{'data-panel-key':'K'}});contexts.set(panel,{$data:raw});
  let marks=[];env.presses=[];env.closes=0;
  env.dialog=(verdict,pm={},opts={})=>{
    for(const mark of marks)mark.open=false;
    marks=[];
    const d=node('div',{attrs:{role:'dialog'},text:verdict+'\nOK',parent:env.ctx.document.body});
    d.textContent=verdict+'\nOK';
    const ok=node('button',{text:'OK',attrs:{type:'button'},parent:d,on:{click:()=>{env.closes++;if(!opts.stuck){d.open=false;mutate();}}}});
    if(opts.extra)node('button',{text:opts.extra,parent:d});
    const source=o.kind==='labels'?c.labelLocationItems.models:o.kind==='categories'?c.labelItems.models:c.answers.models;
    for(const [cls,values] of [['correctAnswer',pm.right||[]],['incorrectAnswer',pm.wrong||[]]])for(const ix of values){
      const n=node('span',{parent:panel,className:'labelLocation '+cls});contexts.set(n,{$data:source[ix]});marks.push(n);
    }
    if(opts.feedback){const fb=node('div',{parent:d,className:'checkAnswer-feedback',text:opts.feedback});d.textContent=verdict+'\n'+opts.feedback+'\nOK';}
    mutate();return d;
  };
  const button=node('button',{text:'Check Answer',attrs:{type:'button'},parent:panel,on:{click:()=>{
    const snap=env.snapshot(env.job,raw);env.presses.push(snap.state);
    const answer=o.verdict?o.verdict(env,env.presses.length,snap):env.presses.length===(o.correctAt||3)?{verdict:'Correct'}:{verdict:'Incorrect'};
    if(answer){const show=()=>{env.dialog(answer.verdict,answer.marks||{},answer);if(answer.change)input.attributes.selectedAnswers=answer.change;if(answer.lock)input.attributes.locked=true;if(answer.confirm)env.ctx.confirm('Continue?');};
      if(answer.delay)env.after(answer.delay,show);else show();}
  }}});contexts.set(button,{$data:raw});
  if(o.oldGuard)Object.defineProperty(env.ctx,'LtGuard',{value:{version:1}});
  env.loadShared('lt_guard');
  if(o.signal)env.ctx.LtNet={install(){},expect(){},clientSig:()=>"synthetic",waitForSave:async()=>({save_signal:o.signal})};
  else env.loadShared('lt_net');env.ctx.LtNet.install(env.ctx);
  env.load(F.LIB_SRC,'fill_lib.js');env.load(BODY,'check_answer.js');env.snapshot=env.ctx.LtCheck.snapshot;
  // Label models use their real write helper with a synthetic keyed network acknowledgement.
  if(o.kind){state.uuids=F.uuidFor(1);env.writes=0;env.save=()=>{env.request('PUT','/api/panels/'+F.uuidFor(1)+'/state',JSON.stringify({uuid:F.uuidFor(1)}),{latency:100});};}
  const kind=o.kind||'select';
  env.job={item:'p1-K',panel_key:'K',kind,type:kind==='select'?'multiple-choice':kind==='labels'?'image-labeling':'categorization',attempts:'multi',multiselect:!!o.multi,
    opt_h:labels.map(F.L0.djb2),pool_h:labels.map(F.L0.djb2),targets:kind==='categories'?(o.bins||['X','Y']).length:labels.length};
  const draft=env.ctx.LtCheck.snapshot(env.job,raw);env.job.draft_state=draft.state;env.job.draft_h=draft.h+'/'+draft.ih;
  env.cfg={module_id:'c1-r101',run_id:101,course_id:1,instance:'inst0001',origin:F.ORIGIN,page:1,enabled:true,policy:{enabled:true,config:'x'},rat_page:status,
    dialog_max_ms:15000,save_wait:{...F.SAVE_WAIT,calibration:{version:1,method:'PUT',path_template:o.kind?F.NET_CAL.path_template:'/api/panels/K/state',client_sig:env.ctx.LtNet.clientSig()}}};
  env.cfg.classification_pin={title:'Page 1',report:false,checkpoint:false,content:{},
    items:{K:{plugin_key:raw.attributes.pluginKey,attempts:'multi',assessment_type:'instantFeedback'}}};
  env.raw=raw;env.input=input;env.content=c;env.button=button;env.panel=panel;env.node=node;env.contexts=contexts;env.mutate=mutate;
  env.run=async(jobs=[env.job])=>{
    let done=false,result,error;
    const policy={mode:'plan',origin:F.ORIGIN,run_id:101,course_id:1,instance:'inst0001',mapped:true,page_map:{1:1},check_answer_keys:jobs.map(j=>[1,j.panel_key])};
    const promise=env.ctx.LtGuard.run(policy,()=>env.ctx.LtCheck.run(env.cfg,jobs)).then(r=>{done=true;result=r;},e=>{done=true;error=e;});
    for(let i=0;!done&&i<5000;i++)await env.clock.advance(25);
    await promise;if(error)throw error;return result;
  };
  return env;
}
module.exports={setup,status,SRC,BODY};
