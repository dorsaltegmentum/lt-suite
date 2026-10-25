// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
'use strict';
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const cp = require('node:child_process');
const fs = require('node:fs');
const {gzipSync, gunzipSync} = require('node:zlib');
const {webcrypto} = require('node:crypto');
const H = require('./harness.js');
const path = require('node:path');
const os = require('node:os');
const BASE = fs.mkdtempSync(path.join(os.tmpdir(), 'lt-cache-review-'));
before(() => {
  const p = cp.spawnSync(process.env.PYTHON || 'python3', [path.join(__dirname, 'emit_cache_fixture.py'), BASE],
    {encoding:'utf8', env:{...process.env, PYTHONIOENCODING:'utf-8'}});
  assert.equal(p.status, 0, p.stderr);
});
after(() => fs.rmSync(BASE, {recursive:true, force:true}));
const PREFIX = 'lt-suite:js:', INDEX = PREFIX + 'index';
const BODY = 'globalThis.__ran = true; return {ok:true,n:__LT_ARGS__.n};';
function render(options = {}, body = BODY) {
  const p = cp.spawnSync(process.env.PYTHON || 'python3', ['-c',
    'import sys,json; from ltcore.ltjs import render_source; x=json.load(sys.stdin); print(render_source(x["body"],name="probe",**x["opts"]))'],
    {input:JSON.stringify({body,opts:{stub:true,cacheable:true,...options}}), encoding:'utf8',
     env:{...process.env,PYTHONPATH:H.REPO+'/shared',PYTHONIOENCODING:'utf-8'}});
  assert.equal(p.status,0,p.stderr); return p.stdout;
}
const want = s => /const WANT = "([a-f0-9]{64})"/.exec(s)[1];
const payload = s => /const B64 = "([^"]*)"/.exec(s)[1];
function storage() {
  const data = new Map(), writes = [];
  return {data,writes,get length(){return data.size;},key:i=>[...data.keys()][i]??null,
    getItem:k=>data.get(k)??null,setItem:(k,v)=>{writes.push([k,String(v)]);data.set(k,String(v));},removeItem:k=>data.delete(k)};
}
function env() {const e=H.makeEnv();e.ctx.localStorage=storage();return e;}

test('inline review: cache mutation after read cannot replace the captured immutable payload', async()=>{
  const e=env(), src=render(), key=PREFIX+want(src);
  e.ctx.localStorage.setItem(key,payload(src));
  e.ctx.crypto={subtle:{digest:async(...a)=>{e.ctx.localStorage.setItem(key,'AAAA');return webcrypto.subtle.digest(...a);}}};
  assert.equal((await e.runRendered(render({cache:true,args:{n:2}}))).n,2);
  assert.equal(e.ctx.__ran,true);
  e.ctx.__ran=undefined;
  assert.equal((await e.runRendered(render({cache:true}))).need,'payload');
  assert.equal(e.ctx.__ran,undefined);
});

test('inline review: replacement atob that changes decoded bytes is rejected', async()=>{
  const e=env();e.ctx.atob=()=> 'bad gzip';
  assert.equal((await e.runRendered(render())).fatal,'stub-hash-mismatch');
  assert.equal(e.ctx.__ran,undefined);
});

test('inline review: corrupt cache cleanup must preserve a valid replacement written during loading', async()=>{
  const e=env(),src=render(),key=PREFIX+want(src),good=payload(src);
  e.ctx.localStorage.setItem(key,gzipSync('async()=>({changed:true})').toString('base64'));
  e.ctx.localStorage.setItem(INDEX,JSON.stringify([key]));
  const pending=e.runRendered(render({cache:true}));
  e.ctx.localStorage.setItem(key,good);
  assert.equal((await pending).need,'payload');
  assert.equal(e.ctx.__ran,undefined);
  assert.equal(e.ctx.localStorage.getItem(key),good);
  assert.deepEqual(JSON.parse(e.ctx.localStorage.getItem(INDEX)),[key]);
});

test('inline review: replacement DecompressionStream that changes output is rejected', async()=>{
  const e=env();e.ctx.DecompressionStream=class {constructor(){return new TransformStream({transform(c,t){t.enqueue(new Uint8Array([65]));}});}};
  assert.equal((await e.runRendered(render())).fatal,'stub-hash-mismatch');
  assert.equal(e.ctx.__ran,undefined);
});

test('inline review limitation: replacement TextDecoder executes different code in gzip and legacy stubs', async()=>{
 for (const compress of [true, false]) {
  const e=env();
  e.ctx.TextDecoder=class {decode(){return 'async () => {globalThis.__injected=true;return {ok:true,injected:true};}';}};
  const r=await e.runRendered(render({compress}));
  assert.equal(r.injected,true);
  assert.equal(e.ctx.__injected,true);
  assert.equal(e.ctx.LtGuard,undefined);
 }
});

test('inline review limitation: replacement Function can ignore verified source', async()=>{
  const e=env();
  e.load("globalThis.Function=function(){return ()=>async()=>{globalThis.__injected=true;return {injected:true};};};");
  assert.equal((await e.runRendered(render())).injected,true);
  assert.equal(e.ctx.LtGuard,undefined);
});

test('inline review limitation: replacement crypto digest can mutate bytes after authentic digest', async()=>{
  const e=env();
  e.ctx.crypto={subtle:{digest:async(algorithm,bytes)=>{
    const d=await webcrypto.subtle.digest(algorithm,bytes);
    const changed=Buffer.from(Buffer.from(bytes).toString().replace('__ran','__bad'));
    bytes.set(changed);return d;
  }}};
  await e.runRendered(render());assert.equal(e.ctx.__bad,true);assert.equal(e.ctx.__ran,undefined);
});

test('inline review limitation: Array prototype replacement can forge compared hash', async()=>{
  const e=env(),src=render();
  e.ctx.localStorage.setItem(PREFIX+want(src),gzipSync('async()=>{globalThis.__injected=true;return {injected:true};}').toString('base64'));
  e.load(`{const original=Array.prototype.join;Array.prototype.join=function(sep){if(this.length===32&&sep==='')return '${want(src)}';return original.call(this,sep);};}`);
  assert.equal((await e.runRendered(render({cache:true}))).injected,true);
});

test('inline review limitation: JSON replacement can copy current inputs into localStorage', async()=>{
  const e=env();
  e.load("{const parse=JSON.parse;JSON.parse=function(s,...r){if(s.includes('sink-secret')||s.includes('policy-secret'))localStorage.setItem('stolen-json-'+localStorage.length,s);return parse(s,...r);};}");
  await e.runRendered(render({args:{token:'sink-secret'},policy:{mode:'block-all',marker:'policy-secret'}}));
  assert.ok([...e.ctx.localStorage.data.values()].some(s=>s.includes('sink-secret')));
  assert.ok([...e.ctx.localStorage.data.values()].some(s=>s.includes('policy-secret')));
});

test('inline review limitation: Object.freeze replacement can retain current token', async()=>{
  const e=env();
  e.load("{const freeze=Object.freeze;Object.freeze=function(o){if(o&&o.token)localStorage.setItem('stolen-freeze',o.token);return freeze(o);};}");
  await e.runRendered(render({args:{token:'sink-secret'}}));
  assert.equal(e.ctx.localStorage.getItem('stolen-freeze'),'sink-secret');
});

test('inline review: altered storage accessor returning wrong code still fails with intact intrinsics', async()=>{
  const e=env();e.ctx.localStorage.getItem=()=>gzipSync('async()=>({injected:true})').toString('base64');
  assert.equal((await e.runRendered(render({cache:true}))).need,'payload');
  assert.equal(e.ctx.LtGuard,undefined);
});

test('inline review: quota failure must preserve the six existing cache entries', async()=>{
  const e=env();
  for(let i=0;i<6;i++)await e.runRendered(render({},BODY+'\nvoid '+i+';'));
  const before=[...e.ctx.localStorage.data.entries()].sort();
  const old=e.ctx.localStorage.setItem;
  e.ctx.localStorage.setItem=(k,v)=>{if(k!==INDEX&&!e.ctx.localStorage.data.has(k))throw Error('QuotaExceededError');return old(k,v);};
  assert.equal((await e.runRendered(render({},BODY+'\nvoid 99;'))).ok,true);
  assert.deepEqual([...e.ctx.localStorage.data.entries()].sort(),before);
});

test('inline review: index write failure must preserve the existing target entry', async()=>{
  const e=env(),src=render();await e.runRendered(src);
  e.ctx.localStorage.setItem(INDEX,'[]');
  const old=e.ctx.localStorage.setItem;
  e.ctx.localStorage.setItem=(k,v)=>{if(k===INDEX)throw Error('QuotaExceededError');return old(k,v);};
  assert.equal((await e.runRendered(src)).ok,true);
  assert.notEqual(e.ctx.localStorage.getItem(PREFIX+want(src)),null);
});

test('inline review: failed index insertion preserves existing bodies until repair', async()=>{
  const e=env();
  for(let i=0;i<6;i++)await e.runRendered(render({},BODY+'\nvoid '+i+';'));
  const before=[...e.ctx.localStorage.data.entries()];
  const old=e.ctx.localStorage.setItem;
  e.ctx.localStorage.setItem=(k,v)=>{if(k===INDEX)throw Error('quota');return old(k,v);};
  const src=render({},BODY+'\nvoid 99;');
  assert.equal((await e.runRendered(src)).ok,true);
  for(const [k,v] of before)assert.equal(e.ctx.localStorage.getItem(k),v);
  e.ctx.localStorage.setItem=old;
  await e.runRendered(src);
  const indexed=JSON.parse(e.ctx.localStorage.getItem(INDEX));
  assert.equal(indexed.length,6);
  assert.ok(indexed.includes(PREFIX+want(src)));
  assert.equal(e.ctx.localStorage.length,7);
});

test('inline review: inventory failure preserves bodies that cannot be accounted for', async()=>{
  const e=env();
  for(let i=0;i<6;i++)await e.runRendered(render({},BODY+'\nvoid '+i+';'));
  const before=[...e.ctx.localStorage.data.entries()];
  e.ctx.localStorage.key=()=>{throw Error('unreadable');};
  assert.equal((await e.runRendered(render({},BODY+'\nvoid 99;'))).ok,true);
  for(const [k,v] of before)assert.equal(e.ctx.localStorage.getItem(k),v);
});

test('inline review: lost index repair must leave at most six body entries', async()=>{
  const e=env(),sources=[];
  for(let i=0;i<7;i++){const s=render({},BODY+'\nvoid '+i+';');sources.push(s);e.ctx.localStorage.setItem(PREFIX+want(s),payload(s));}
  sources.sort((a,b)=>want(a).localeCompare(want(b)));
  e.ctx.localStorage.setItem(INDEX,'bad-json');
  await e.runRendered(sources[0]);
  const keys=[...e.ctx.localStorage.data.keys()].filter(k=>k!==INDEX);
  const indexed=JSON.parse(e.ctx.localStorage.getItem(INDEX));
  assert.equal(keys.length,6);
  assert.deepEqual(indexed.slice().sort(),keys.slice().sort());
  assert.ok(keys.includes(PREFIX+want(sources[0])));
});

test('inline review: real emitted answer body outside bundle directories skips cache', async()=>{
  const info=JSON.parse(fs.readFileSync(BASE+'/generated.json','utf8')).ordinary;
  const e=env();await e.runRendered(fs.readFileSync(info.path,'utf8'));
  assert.equal(info.cacheable,false);assert.equal(e.ctx.localStorage.length,0);
});

test('inline review: real generated answer script must remain excluded inside browser directory', async()=>{
  const info=JSON.parse(fs.readFileSync(BASE+'/generated.json','utf8')).relocated;
  const e=env();await e.runRendered(fs.readFileSync(info.path,'utf8'));
  const saved=e.ctx.localStorage.getItem(PREFIX+info.body_sha256);
  assert.equal(info.cacheable,false);assert.equal(saved,null);
  assert.equal(e.ctx.localStorage.writes.length,0);
});

test('inline review: chunk globals and receipts exclude current token and policy, then clear assembly', async()=>{
  const p=cp.spawnSync(process.env.PYTHON||'python3',['-c',
    'import json;from ltcore.ltjs import function_text,tail_return,_payload,chunk_text,render_source;'+
    'b='+JSON.stringify(BODY)+';inner=function_text(tail_return(b),name="probe",args=None,policy=None,net=False,parameterized=True);'+
    'payload,want=_payload(inner);print(json.dumps({"want":want,"chunks":[chunk_text(payload[i:i+2000],want,i//2000+1,"expression") for i in range(0,len(payload),2000)],'+
    '"stub":render_source(b,name="probe",stub=True,chunked=True,cacheable=True,args={"n":3,"token":"sink-secret"},policy={"mode":"block-all","marker":"policy-secret"})}))'],
    {encoding:'utf8',env:{...process.env,PYTHONPATH:H.REPO+'/shared',PYTHONIOENCODING:'utf-8'}});
  assert.equal(p.status,0,p.stderr);
  const info=JSON.parse(p.stdout),e=env();
  for(const chunk of info.chunks){
    const r=await e.runRendered(chunk);
    assert.ok(!JSON.stringify([r,e.ctx.__ltSuitePayload]).includes('sink-secret'));
    assert.ok(!JSON.stringify([r,e.ctx.__ltSuitePayload]).includes('policy-secret'));
  }
  const r=await e.runRendered(info.stub);
  assert.equal(r.n,3);assert.equal(e.ctx.__ltSuitePayload[info.want],undefined);
  assert.ok(!JSON.stringify(r).includes('secret'));
  const raw=gunzipSync(Buffer.from(e.ctx.localStorage.getItem(PREFIX+info.want),'base64')).toString();
  assert.ok(!raw.includes('sink-secret'));assert.ok(!raw.includes('policy-secret'));
});
