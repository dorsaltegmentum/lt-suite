// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const cp = require('node:child_process');
const {gzipSync, gunzipSync} = require('node:zlib');
const F = require('./fill_env.js');
const H = F.harness();
const storage = () => {
  const data = new Map();
  return {data, get length() {return data.size;}, key: i => [...data.keys()][i] ?? null,
    getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, String(v)), removeItem: k => data.delete(k)};
};
const policy = (stop = 2) => ({mode:'plan', origin:F.ORIGIN, instance:'inst0001', course_id:1,
  run_id:101, mapped:true, page_map:{1:1, 2:2}, never_indexes:[], stop_before_page:stop});
const args = (text = 'synthetic answer sentinel') => ({method:'fillPage', cfg:F.CFG({page:1, title:'Page 1'}),
  jobs:[F.netJob('K', '<p>' + text + '</p>', 1)], token:'synthetic token sentinel'});
function render(a = args(), p = policy(), form = 'expression') {
  const code = `import json,sys
from pathlib import Path
from ltcore import ltjs
s=json.load(sys.stdin)
p=Path('skills/lt-module-fill/scripts/browser/fill_runner.js')
b=ltjs.browser_body(p)
o=dict(name='fill_runner',stub=True,net=True,cacheable=True,args=s['args'],policy=s['policy'],form=s['form'])
inner=ltjs.function_text(b,name='fill_runner',args=None,policy=None,net=True,parameterized=True,form=s['form'])
packed,want=ltjs._payload(inner)
print(json.dumps(dict(loader=ltjs.render_source(b,cache=True,**o),stub=ltjs.render_source(b,chunked=True,**o),want=want,
chunks=[ltjs.chunk_text(packed[i:i+20000],want,n,s['form']) for n,i in enumerate(range(0,len(packed),20000),1)],
direct=ltjs.render_source(b,name='fill_runner',stub=False,net=True,args=s['args'],policy=s['policy'],form=s['form']))))`;
  const result = cp.spawnSync(process.env.PYTHON || 'python3', ['-c', code], {
    cwd:F.REPO, input:JSON.stringify({args:a, policy:p, form}), encoding:'utf8',
    env:{...process.env, PYTHONPATH:F.REPO + '/shared', PYTHONIOENCODING:'utf-8'}
  });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}
function env(store = storage(), beforeWrite) {
  const e = F.netEnv({net:false, pages:[{title:'Page 1', current:true, panels:e => {
    const panel = F.netTextPanel(e, 'K', 1);
    if (beforeWrite) {
      const original = panel.model.set.bind(panel.model);
      panel.model.set = (...a) => {beforeWrite(e); return original(...a);};
    }
    return [panel];
  }}]});
  e.ctx.localStorage = store;
  return e;
}
async function execute(e, source) {
  let done = false, result, error;
  const promise = e.runRendered(source).then(r => {done=true; result=r;}, err => {done=true; error=err;});
  for (let i=0; !done && i<10000; i++) {
    await e.clock.advance(25);
    await new Promise(setImmediate);
  }
  assert.equal(done, true, 'rendered call completed');
  await promise;
  if (error) throw error;
  return JSON.parse(JSON.stringify(result));
}
async function fallback(e, r) {
  for (const chunk of r.chunks) await execute(e, chunk);
  return execute(e, r.stub);
}
const cachedCode = (e, want) => gunzipSync(Buffer.from(e.ctx.localStorage.getItem('lt-suite:js:' + want), 'base64')).toString();

test('cached-fill: miss writes nothing and chunk fallback shares code across calls', async () => {
  for (const form of ['expression', 'function']) {
    const r = render(args(), policy(), form), e = env();
    assert.equal((await execute(e, r.loader)).need, 'payload');
    assert.equal(e.writes.length, 0);
    const result = await fallback(e, r);
    assert.equal(result.items[0].result, 'written', JSON.stringify(result));
    assert.equal(e.ctx.__ltSuitePayload[r.want], undefined);
    const otherArgs=args('second answer');
    const other = render({...otherArgs, method:'verify', jobs:{1:otherArgs.jobs}}, policy(), form);
    assert.equal(other.want, r.want);
    assert.equal((await execute(e, other.loader)).fresh_load, false);
    const fresh = env(e.ctx.localStorage);
    assert.equal((await execute(fresh, other.loader)).fresh_load, true);
  }
});

for (const [name, corrupt] of [
  ['tampered body', b => gzipSync(gunzipSync(Buffer.from(b, 'base64')).toString().replace('switch (__LT_ARGS__.method)', 'globalThis.compromised=true; switch (__LT_ARGS__.method)')).toString('base64')],
  ['bad base64', () => '!bad!'],
  ['bad gzip', () => Buffer.from('invalid gzip').toString('base64')],
  ['truncated gzip', b => b.slice(0, -8)]
]) test('cached-fill: ' + name + ' executes nothing', async () => {
  const r=render(), e=env();
  await fallback(e, r);
  const key='lt-suite:js:' + r.want;
  e.ctx.localStorage.setItem(key, corrupt(e.ctx.localStorage.getItem(key)));
  const fresh=env(e.ctx.localStorage);
  assert.equal((await execute(fresh, r.loader)).need, 'payload');
  assert.equal(fresh.writes.length, 0);
  assert.equal(fresh.ctx.compromised, undefined);
});

test('cached-fill: truncated chunks clear assembly and execute nothing', async () => {
  const r=render(), e=env();
  const truncated=r.chunks[0].replace(/B64 = "([^"]+)"/, (_, value) => 'B64 = "' + value.slice(0,-4) + '"');
  assert.equal((await execute(e,truncated)).fatal, 'stub-chunk-mismatch');
  assert.equal(e.writes.length,0);
  for (const chunk of r.chunks.slice(0,-1)) await execute(e, chunk);
  assert.equal((await execute(e, r.stub)).fatal, 'stub-hash-mismatch');
  assert.equal(e.ctx.__ltSuitePayload[r.want], undefined);
  assert.equal(e.writes.length, 0);
  assert.equal(e.ctx.localStorage.length, 0);
});

test('cached-fill: call secrets and expected values never enter cached code or chunk globals', async () => {
  const a=args(); a.cfg.ticket='synthetic ticket sentinel';
  const e=env(), r=render(a);
  const result=await fallback(e,r);
  const persisted=cachedCode(e,r.want) + JSON.stringify([...e.ctx.localStorage.data]) + JSON.stringify(e.ctx.__ltSuitePayload);
  for (const secret of [a.token, a.jobs[0].html, String(a.jobs[0].h), a.cfg.ticket, 'synthetic answer sentinel']) {
    assert.equal(persisted.includes(secret), false, secret);
    if (secret !== String(a.jobs[0].h) && secret !== a.cfg.ticket) assert.equal(JSON.stringify(result).includes(secret), false, secret);
  }
  assert.equal(result.ticket,a.cfg.ticket);
  assert.equal(result.items[0].h,a.jobs[0].h);
  assert.equal(e.ctx.__LT_ARGS__, undefined);
  assert.equal(e.ctx.__ltSuitePayload[r.want], undefined);
});

test('cached-fill: current policy and frozen inputs reach the guard before any write', async () => {
  const a=args(), seed=env(), r=render(a);
  await fallback(seed,r);
  let observed=false;
  const e=env(seed.ctx.localStorage, e => {
    observed=true;
    assert.equal(e.ctx.LtGuard.status().ok,true);
    assert.equal(e.load('frozenInputs.every(o=>Object.isFrozen(o))'),true);
    assert.equal(e.load('frozenInputs.some(o=>o.save_wait && Object.isFrozen(o.save_wait))'),true);
    assert.equal(e.load('frozenInputs.some(o=>o.html)'),true);
    assert.equal(e.ctx.LtGuard.allowed(F.ORIGIN + '/i/inst0001/student/courses/1/runs/101/run/page/2').allowed, true);
  });
  e.load(`globalThis.frozenInputs=[]; const freeze=Object.freeze;
    Object.freeze=value=>{const result=freeze(value); if (value && (value.html || value.save_wait)) frozenInputs.push(value); return result;};`);
  const current=render(args('new call answer'),policy(3));
  const result=await execute(e,current.loader);
  assert.equal(result.items[0].result,'written',JSON.stringify(result));
  assert.equal(observed,true);
  assert.equal(e.ctx.__ltSuitePayload, undefined);
});

test('cached-fill: page supplied arguments cannot drive the cached runner', async () => {
  const a=args(), seed=env(), r=render(a);
  await fallback(seed,r);
  const e=env(seed.ctx.localStorage);
  e.ctx.__LT_ARGS__={...args('page injected answer'),method:'fillPages'};
  e.ctx.__ltArgs=e.ctx.__LT_ARGS__;
  e.ctx.__ltSuitePayload={[r.want]:['page supplied payload']};
  const result=await execute(e,r.loader);
  assert.equal(result.items[0].result,'written',JSON.stringify(result));
  assert.equal(e.made[0][0].model.attributes.answer,a.jobs[0].html);
});

test('cached-fill: a page outside this call plan refuses with the existing guard receipt', async () => {
  const seed=env(), r=render();
  await fallback(seed,r);
  const e=env(seed.ctx.localStorage);
  const a=args(); a.cfg.run_id=102;
  const bad=render(a,{...policy(),run_id:102});
  const result=await execute(e,bad.loader);
  assert.equal(result.fatal,'wrong-run',JSON.stringify(result));
  assert.equal(e.writes.length,0);
});

test('cached-fill: probe dispatch reads the page without writing any answer', async () => {
  const e=env(), r=render({method:'probe'});
  const result=await fallback(e,r);
  assert.equal(result.ok,true,JSON.stringify(result));
  assert.equal(result.panels.length,1);
  assert.equal(e.writes.length,0);
});

for (const kind of [undefined,'categories','labels']) {
  test('cached-fill: check receipts and fresh verification retain ' + (kind || 'select') + ' hashes', async () => {
    const {setup}=require('./check_env.js');
    const legacy=setup({kind}), cached=setup({kind});
    cached.ctx.localStorage=storage();
    const expected=await legacy.run();
    const p={...policy(),check_answer_keys:[[1,'K']]};
    const r=render({method:'check',cfg:cached.cfg,jobs:[cached.job]},p);
    const result=await fallback(cached,r);
    const fields=['module','page','mode','writes','save_signal','policy','stop'];
    for(const k of fields) assert.deepEqual(result[k],expected[k],k);
    assert.deepEqual(result.items,JSON.parse(JSON.stringify(expected.items)));
    assert.deepEqual(cached.presses,legacy.presses);
    const fresh=setup({kind});
    fresh.ctx.localStorage=cached.ctx.localStorage;
    const cfg={module_id:'c1-r101',run_id:101,origin:F.ORIGIN,instance:'inst0001',course_id:1};
    const job={...cached.job,final_h:result.items[0].final_h};
    const verify=render({method:'checkVerify',cfg,jobs:{1:[job]}},p);
    assert.equal(verify.want,r.want);
    const receipt=await execute(fresh,verify.loader);
    assert.equal(receipt.mode,'check-verify',JSON.stringify(receipt));
    assert.equal(receipt.fresh_load,true);
  });
}

test('cached-fill: changed batch calibration refuses before writing', async () => {
  const a=args();
  a.cfg.save_wait.calibration={version:1,method:'PUT',path_template:'/api/panels/{uuid}/state',source:'net',client_sig:'obsolete-client'};
  a.method='fillPages'; a.cfg.batch_pages=[1]; a.jobs=[{page:1,jobs:a.jobs}];
  const e=env(), r=render(a);
  const result=await fallback(e,r);
  assert.equal(result.fatal,'calibration-stale',JSON.stringify(result));
  assert.equal(e.writes.length,0);
});

for (const method of ['fillPage', 'fillPages', 'verify', 'probe', 'check', 'checkVerify']) {
  test('cached-fill: completed ' + method + ' leaves zero global writes and presses', async () => {
    const {setup}=require('./check_env.js');
    let e=method.startsWith('check') ? setup() : method==='fillPages' ? F.netEnv({pages:[{title:'Page 1',current:true,panels:e=>[F.netTextPanel(e,'K',1)]}]}) : env();
    e.ctx.localStorage=storage();
    const checkCfg=method.startsWith('check') ? e.cfg : null;
    const checkJobs=method.startsWith('check') ? [e.job] : null;
    delete e.ctx.LtFillLib; delete e.ctx.LtCheck; delete e.ctx.probeLtPanels;
    let a=args();
    if (method==='probe') a={method};
    else if (method.startsWith('check')) a={method,cfg:checkCfg,jobs:method==='check' ? checkJobs : {1:checkJobs}};
    else if (method==='verify') a={...a,method,jobs:{1:a.jobs}};
    else if (method==='fillPages') a={...a,method,cfg:{...a.cfg,batch_pages:[1],save_wait:{...a.cfg.save_wait,calibration:{...F.NET_CAL,client_sig:e.ctx.LtNet.clientSig()}}},jobs:[{page:1,title_hash:F.L0.titleHash('Page 1'),jobs:a.jobs}]};
    const r=render(a,{...policy(),check_answer_keys:[[1,'K']]});
    const contextFor=e.ctx.ko.contextFor;
    e.ctx.ko.contextFor=(...values)=>{
      for (const name of ['LtFillLib','LtCheck','probeLtPanels']) assert.equal(e.ctx[name],undefined,name);
      return contextFor(...values);
    };
    for (const source of (method==='check' ? [null,r.loader] : [null,r.loader,r.direct])) {
      if (source && method==='check') {
        const store=e.ctx.localStorage;
        e=setup();e.ctx.localStorage=store;
        delete e.ctx.LtFillLib;delete e.ctx.LtCheck;delete e.ctx.probeLtPanels;
        const contextFor=e.ctx.ko.contextFor;
        e.ctx.ko.contextFor=(...v)=>{
          for(const name of ['LtFillLib','LtCheck','probeLtPanels'])assert.equal(e.ctx[name],undefined,name);
          return contextFor(...v);
        };
      }
      const result=source ? await execute(e,source) : await fallback(e,r);
      assert.equal(result.ok,true,JSON.stringify(result));
      const writes=Array.isArray(e.writes) ? e.writes.length : e.writes || 0;
      const presses=e.presses?.length || 0;
      const attack=args('page chosen answer after completed call');
      if (e.ctx.LtFillLib?.fillPage) await execute(e,`async () => await LtFillLib.fillPage(${JSON.stringify(attack.cfg)},${JSON.stringify(attack.jobs)})`);
      if (e.ctx.LtCheck?.run && checkCfg) await execute(e,`async () => await LtGuard.run(${JSON.stringify({...policy(),check_answer_keys:[[1,'K']]})},()=>LtCheck.run(${JSON.stringify(checkCfg)},${JSON.stringify(checkJobs)}))`);
      assert.equal(Array.isArray(e.writes) ? e.writes.length : e.writes || 0,writes);
      assert.equal(e.presses?.length || 0,presses);
      for (const name of ['LtFillLib','LtCheck','probeLtPanels']) assert.equal(e.ctx[name],undefined,name);
    }
  });
}

function protocol(a, form, chunked) {
  const code=`import json,sys,tempfile,subprocess
from pathlib import Path
s=json.load(sys.stdin)
with tempfile.TemporaryDirectory() as folder:
 p=Path(folder)
 (p/'config.toml').write_text(chr(10).join(['[fill.check_answer]','enabled = true','']),encoding='utf-8')
 (p/'args.json').write_text(json.dumps(s['args']),encoding='utf-8')
 plan=dict(origin='https://lt.example.invalid',instance='inst0001',course_id=1,run_id=101,pages=[dict(page_index=1,page_no=1)],check_answer_keys=[[1,'K']])
 (p/'plan.json').write_text(json.dumps(plan),encoding='utf-8')
 target='probe_panels' if s['args']['method']=='probe' else 'fill_runner'
 target=str(Path('skills/lt-module-fill/scripts/browser')/(target+'.js'))
 base=[sys.executable,'-m','ltcore.ltjs','render',target,'--config',str(p/'config.toml'),'--plan',str(p/'plan.json'),'--args-file',str(p/'args.json'),'--form',s['form'],'--out-dir',str(p/'js')]
 def render(flags):
  result=subprocess.run(base+flags,capture_output=True,encoding='utf-8')
  if result.returncode: raise RuntimeError(result.stderr)
  meta=json.loads(result.stdout)
  return dict(meta=meta,source=Path(meta['path']).read_text(encoding='utf-8'),chunks=[dict(meta=c,source=Path(c['path']).read_text(encoding='utf-8')) for c in meta['chunks']])
 print(json.dumps(dict(loader=render(['--cache']),fallback=render(['--stub']+(['--chunk-bytes','20000'] if s['chunked'] else [])))))`;
  const r=cp.spawnSync(process.env.PYTHON || 'python3',['-c',code],{cwd:F.REPO,input:JSON.stringify({args:a,form,chunked}),encoding:'utf8',env:{...process.env,PYTHONPATH:F.REPO+'/shared',PYTHONIOENCODING:'utf-8'}});
  assert.equal(r.status,0,r.stderr);
  return JSON.parse(r.stdout);
}
for (const method of ['probe','fillPage','verify','check']) {
  test('cached-fill: documented fallback sequence executes ' + method, async () => {
    for (const form of ['expression','function']) for (const chunked of [false,true]) {
      const e=method==='check' ? require('./check_env.js').setup() : env();
      e.ctx.localStorage=storage();
      let a=method==='probe' ? {method} : method==='check' ? {method,cfg:e.cfg,jobs:[e.job]} : args();
      if (method==='verify') a={...a,method,jobs:{1:a.jobs}};
      const r=protocol(a,form,chunked);
      assert.equal((await execute(e,r.loader.source)).need,'payload');
      assert.equal(r.loader.meta.body_sha256,r.fallback.meta.body_sha256);
      for (const c of r.fallback.chunks) {
        const receipt=await execute(e,c.source);
        for (const key of ['i','len','sha16']) assert.equal(receipt[key],c.meta[key]);
      }
      const result=await execute(e,r.fallback.source);
      assert.equal(result.ok,true,JSON.stringify(result));
      assert.equal(result.need,undefined);
      assert.equal((await execute(e,r.loader.source)).need,undefined);
    }
  });
}

test('cached-fill: R14 completed probe rejects arbitrary writer and check jobs', async () => {
  const e=require('./check_env.js').setup();
  e.ctx.localStorage=storage();
  delete e.ctx.LtFillLib; delete e.ctx.LtCheck; delete e.ctx.probeLtPanels;
  assert.equal((await fallback(e,render({method:'probe'}))).ok,true);
  const attack=args('page chosen answer after completed probe');
  const r=await execute(e,`async () => {
    if (globalThis.LtFillLib) await LtFillLib.fillPage(${JSON.stringify(attack.cfg)},${JSON.stringify(attack.jobs)});
    if (globalThis.LtCheck) await LtGuard.run(${JSON.stringify({...policy(),check_answer_keys:[[1,'K']]})},()=>LtCheck.run(${JSON.stringify(e.cfg)},${JSON.stringify([e.job])}));
    return {ok:true};
  }`);
  assert.equal(r.ok,true);
  assert.equal(e.presses.length,0);
  assert.equal(e.writes.length,0);
  for (const name of ['LtFillLib','LtCheck','probeLtPanels']) assert.equal(e.ctx[name],undefined,name);
});

for (const method of ['fillPage','verify']) {
  test('cached-fill: served ' + method + ' keeps writers private during and after calls',async()=>{
    const a=args();
    if (method==='verify') {a.method=method;a.jobs={1:a.jobs};}
    const code=`import json,sys,runpy
from ltcore import ltjs
s=json.load(sys.stdin)
e=runpy.run_path('skills/lt-module-fill/scripts/emit_fill.py')
b=e['script_body']('LtFillLib.'+s['args']['method']+'(__LT_ARGS__.cfg,__LT_ARGS__.jobs)','http://127.0.0.1:8733')
print(json.dumps(ltjs.render_source(b,name='served',stub=False,net=True,args=s['args'],policy=s['policy'])))`;
    const result=cp.spawnSync(process.env.PYTHON || 'python3',['-c',code],{cwd:F.REPO,input:JSON.stringify({args:a,policy:policy()}),encoding:'utf8',env:{...process.env,PYTHONPATH:F.REPO+'/shared',PYTHONIOENCODING:'utf-8'}});
    assert.equal(result.status,0,result.stderr);
    const e=env();
    delete e.ctx.LtFillLib;delete e.ctx.probeLtPanels;
    const inspect=()=>{for(const name of ['LtFillLib','LtCheck','probeLtPanels'])assert.equal(e.ctx[name],undefined,name);};
    const contextFor=e.ctx.ko.contextFor;e.ctx.ko.contextFor=(...v)=>{inspect();return contextFor(...v);};
    e.ctx.AbortController=AbortController;
    e.ctx.fetch=async url=>({ok:true,text:async()=>require('node:fs').readFileSync(F.BROWSER+'/'+new URL(url).pathname.split('/').pop(),'utf8')});
    inspect();
    const receipt=await execute(e,JSON.parse(result.stdout));
    assert.equal(receipt.ok,true,JSON.stringify(receipt));
    inspect();
    assert.equal(e.writes.length,method==='fillPage'?1:0);
  });
}
