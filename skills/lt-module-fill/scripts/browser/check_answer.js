// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* One displayed page, sequential presses, finite candidates and event waits.
 * Inputs pin captured author text; receipts contain indices and hashes.
 * Feedback is untrusted data.
 */
(function (G) {
  'use strict';
  const TYPES = ['multiple-choice', 'image-labeling', 'drag-and-drop-labeling', 'categorization'];
  const VERDICT_MAX_MS = 10000, CALL_DEADLINE_MS = 90000;
  const DENY = [
    /submit/i, /commit/i, /finish/i, /finalis|finaliz/i, /hand\s*in/i,
    /checkpoint/i, /complete\s+(this|the)/i, /finish\s+attempt/i,
    /cannot\s+be\s+(undone|changed)/i, /final\s+answer/i, /once\s+you\s+(submit|continue)/i,
  ];
  const L = () => G.LtFillLib;
  const clock = () => G.__ltClock || {now: () => Date.now()};
  const text = el => String(el && (el.innerText ?? el.textContent) || '').trim();
  const visible = el => {
    if (!el || !el.getClientRects || !el.getClientRects().length) return false;
    const s = G.getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity) > 0;
  };
  const dialogs = () => Array.from(G.document.querySelectorAll('[role=dialog]')).filter(visible);
  const q = (el, sel) => Array.from(el.querySelectorAll(sel));
  const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  function encode(kind, state) {
    return kind === 'select' ? state.slice().sort((a,b) => a-b).join(',') :
      state.slice().sort((a,b) => a[0]-b[0] || a[1]-b[1]).map(p => p.join(':')).join(',');
  }
  function decode(kind, state) {
    if (!state) return [];
    return kind === 'select' ? state.split(',').map(Number) : state.split(',').map(s => s.split(':').map(Number));
  }
  function snapshot(job, raw) {
    const f = L(), inp = f.inputOf(raw);
    const pairs = job.kind === 'select' ? f.liveSelection(inp) : job.kind === 'labels' ? f.liveLinks(inp) : f.livePlacements(inp).map(p => [p[1],p[0]]);
    const state = encode(job.kind, pairs);
    const canon = job.kind === 'select' ? 'sel:' + state : (job.kind === 'labels' ? 'labi:' : 'cati:') +
      pairs.map(p => p[1] + '\t' + p[0]).sort().join('\n');
    return {state, ih: f.djb2(canon), h: f.liveHash(job, raw).h};
  }
  const pairHash = s => s.h + '/' + s.ih;
  const trips = () => G.LtGuard && G.LtGuard.trips ? G.LtGuard.trips() : 0;
  function boundPanel(el, raw) {
    try {
      const c = G.ko.contextFor(el);
      return !!c && [c.$data, c.$parent, ...(c.$parents || [])].some(v => v === raw || L().A(v) === L().A(raw));
    } catch (e) { return false; }
  }
  function panelElement(raw) {
    return q(G.document, '[data-panel-key], .panel').find(el => boundPanel(el, raw)) || null;
  }
  function shape(job, raw) {
    const f = L(), c = f.contentOf(raw);
    if (job.kind === 'select') return f.U(c.multiselect) === job.multiselect && eq(f.models(c.answers).map(a => f.djb2(f.pageText(f.U(f.A(a).answer)))), job.opt_h);
    return eq(f.labelPool(c).map(t => f.djb2(f.pageText(t))), job.pool_h) &&
      f.models(job.kind === 'labels' ? c.labelLocationItems : c.categoryItems).length === job.targets;
  }
  const authorText = value => {
    let h = String(value ?? '').replace(/\p{Cf}/gu, '')
      .replace(/&#(x[0-9a-f]+|[0-9]+);?/gi, (entity, code) => {
        const point = code[0].toLowerCase() === 'x' ? parseInt(code.slice(1), 16) : Number(code);
        return point <= 0x10ffff && /\p{Cf}/u.test(String.fromCodePoint(point)) ? '' : entity;
      })
      .replace(/&(?:shy|zwnj|zwj|lrm|rlm|ZeroWidthSpace|NoBreak|ApplyFunction|af|InvisibleTimes|it|InvisibleComma|ic|NegativeMediumSpace|NegativeThickSpace|NegativeThinSpace|NegativeVeryThinSpace);/g, '');
    if (h.includes('<') || h.includes('&')) {
      if (!G.DOMParser) return null;
      const doc = new G.DOMParser().parseFromString(h, 'text/html');
      let removed = 0;
      const visit = parent => {
        for (const node of Array.from(parent.childNodes)) {
          if (node.nodeType === 8) { node.remove(); removed++; continue; }
          if (node.nodeType !== 1) continue;
          const style = String(node.getAttribute('style') || '').replace(/\/\*[\s\S]*?\*\//g, '');
          const css = property => node.style ? node.style.getPropertyValue(property).trim().toLowerCase()
            : ((style.match(new RegExp(`(?:^|;)\\s*${property}\\s*:\\s*([^;]+)`, 'i')) || [])[1] || '')
              .replace(/!important/gi, '').trim().toLowerCase();
          const hidden = node.hasAttribute('hidden') || node.getAttribute('aria-hidden')?.toLowerCase() === 'true'
            || css('display') === 'none' || css('visibility') === 'hidden'
            || /^(?:0|0px|0em|0rem|0%)$/.test(css('font-size')) || /^0(?:\.0+)?$/.test(css('opacity'));
          if (hidden || ['script', 'style', 'template', 'noscript'].includes(node.localName)) {
            node.remove(); removed++;
          } else visit(node);
        }
      };
      visit(doc);
      // Keep the historical formatter byte-for-byte for normal authored markup.
      // A modified tree is serialized only after unsafe subtrees have been discarded.
      if (removed) h = (doc.body?.innerHTML || '').replace(/\u00a0/g, ' ').replace(/\u2019/g, "'");
    }
    return String(h)
    .replace(/<\s*br\s*\/?>/gi, '\n')
    .replace(/<\s*li[^>]*>/gi, '\n- ')
    .replace(/<\s*\/\s*(p|div|li|ul|ol|h[1-6]|tr|table|blockquote)\s*>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&#39;|&rsquo;/g, "'").replace(/&quot;/g, '"').replace(/&#43;/g, '+')
    .replace(/[ \t]+/g, ' ').replace(/ *\n */g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  };
  const normalText = s => String(s ?? '').replace(/[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/g,' ').replace(/^ | $/g,'');
  function sameClassification(cfg, page) {
    const pin=cfg.classification_pin, f=L();
    if (!pin || !pin.content || !pin.items) return false;
    const title=authorText(f.U(page.title));
    if (title===null || normalText(title)!==pin.title ||
        ('reportInfoPanels' in page)!==pin.report ||
        ('committed' in page && !('allPanels' in page))!==pin.checkpoint) return false;
    const content=Object.create(null), items=Object.create(null), by=f.panelsOf(page);
    const drop=(cfg.drop_text_patterns||[]).map(p=>new RegExp(p));
    for (const [key,raw] of Object.entries(by)) {
      const plugin=(()=>{const a=f.A(raw)||{};return typeof raw.pluginKey==='function'?String(raw.pluginKey()):String(f.U(a.pluginKey));})();
      const st=f.stateOf(raw), c=f.contentOf(raw);
      if (plugin==='subpage') {
        items[key]={plugin_key:plugin,attempts:'multi',assessment_type:'noAssessment'};continue;
      }
      if ('input' in st) {
        items[key]={plugin_key:plugin,attempts:f.U(c.singleAttempt)===true?'single':'multi',
          assessment_type:String(f.U(c.assessmentType)||'noAssessment')};continue;
      }
      let value='';
      if (plugin==='kcTextArea') {
        value=authorText(f.U(c.theText));
        if (value===null) return false;
        if (!value || drop.some(re=>re.test(value))) continue;
      } else if (plugin==='image') value=authorText(f.U(c.caption));
      else if (plugin==='table') {
        const rows=f.models(c.rows).map(row=>{
          const a=f.A(row)||{}, cells=f.models(a.cells).length?f.models(a.cells):f.models(a.columns);
          return cells.map(cell=>{const a=f.A(cell)||{};return authorText(f.U(a.content??a.text??''));});
        });
        if (rows.some(row=>row.includes(null))) return false;
        value=rows.map(row=>row.join(' | ')).filter(row=>row.replace(/[ |]/g,'').length).join('\n');
        if (!value) continue;
      } else if (plugin==='hotspot') {
        const popups=f.models(c.hotspots).map(raw=>{
          const a=f.A(raw)||{}, title=authorText(f.U(a.labelTitle)), text=authorText(f.U(a.labelDescription));
          return title===null||text===null?null:title&&text?title+': '+text:title||text;
        });
        if (popups.includes(null)) return false;
        value=popups.filter(Boolean).join('\n\n');
      }
      if (value===null) return false;
      content[key]={plugin_key:plugin,text:normalText(value)};
    }
    const same=(a,b)=>eq(Object.keys(a).sort(),Object.keys(b).sort()) &&
      Object.keys(a).every(k=>eq(a[k],b[k]));
    return same(content,pin.content) && same(items,pin.items);
  }

  function live(cfg, job, expected, retried) {
    const f = L();
    if (dialogs().length) return {reason: 'dialog-present', call: true};
    const root = f.rootOf(), flat = root ? f.flatPages(root) : [], page = f.pageAt(flat, cfg.page);
    if (!page || f.U(page.current) !== true) return {reason: 'page-not-displayed'};
    const passed=f.checkPassedSingles(cfg.single_attempt_locked,flat,null,cfg.page);
    if (passed) return {reason:passed.fatal,call:true};
    const raw = f.panelsOf(page)[String(job.panel_key)];
    if (!raw) return {reason: 'no-panel'};
    const c = f.contentOf(raw), st = f.stateOf(raw), a = f.A(raw), inp = f.A(f.inputOf(raw)) || {};
    if (f.U(c.singleAttempt) !== false) return {reason: f.U(c.singleAttempt) === true ? 'single-attempt' : 'attempts-unknown'};
    if (f.U(c.assessmentType) !== 'instantFeedback') return {reason: 'assessment-live'};
    if (f.U(inp.locked) === true) return {reason: 'locked'};
    const ro = [a.isReadOnly, st.isReadOnly, c.isReadOnly].map(f.U);
    if (ro.includes(true)) return {reason: 'read-only'};
    if (!ro.some(v => typeof v === 'boolean')) return {reason: 'read-only-unknown'};
    if (!shape(job, raw)) return {reason: 'shape-changed'};
    if (!sameClassification(cfg,page)) {
      Object.defineProperty(G.document,'__ltCheckUnsafe',{value:true});
      return {reason:'classification-changed',call:true};
    }
    const snap = snapshot(job, raw);
    if (snap.state !== expected) return {reason: retried ? 'changed-since-last-write' : 'changed-since-verify'};
    const el = panelElement(raw);
    if (!el) return {reason: 'no-panel-element'};
    if (G.LtGuard.commitControl(el)) return {reason: 'commit-control-present'};
    const buttons = q(el, 'button').filter(b => visible(b) && /^\s*check\s+answer\s*$/i.test(text(b)) && boundPanel(b, raw));
    if (!buttons.length) return {reason: 'no-button'};
    if (buttons.length !== 1) return {reason: 'several-buttons'};
    if (buttons[0].disabled || buttons[0].getAttribute('aria-disabled') === 'true') return {reason: 'button-disabled'};
    return {raw, button: buttons[0], el, snap};
  }
  /* Observe before the action. Synchronous DOM creation is included. No polling sleeps. */
  function observe(test, cap, action) {
    return new Promise(resolve => {
      let done = false, timer;
      const finish = v => { if (done) return; done = true; observer.disconnect(); G.clearTimeout(timer); resolve(v); };
      const check = () => { const v = test(); if (v) finish(v); };
      const observer = new G.MutationObserver(check);
      observer.observe(G.document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['class','style','hidden','aria-hidden']});
      timer = G.setTimeout(() => finish(null), Math.max(0, cap));
      try { if (action) action(); check(); } catch (e) { finish({error: true,reason:e.checkReason}); }
    });
  }
  function press(button) { button.click(); }
  function close(button) { button.click(); }
  function readVerdict() {
    const ds = dialogs();
    if (!ds.length) return null;
    if (ds.length !== 1) return {reason: 'verdict-unread'};
    const dialog = ds[0], raw = text(dialog), buttons = q(dialog, 'button');
    if (DENY.some(p => p.test(raw))) return {reason: 'dialog-refused'};
    const labels = buttons.map(text);
    const lines = raw.split(/\r?\n/).map(s => s.trim()).filter(s => s && !labels.includes(s));
    const verdicts = lines.filter(s => /^(Correct|Incorrect|Partially correct)$/i.test(s));
    if (verdicts.length !== 1 || lines.some(s => /^model answer\b/i.test(s)) || buttons.length !== 1 || !/^ok$/i.test(text(buttons[0])) || !visible(buttons[0]) || buttons[0].disabled) return {reason: 'verdict-unread'};
    const feedback = q(dialog, '.checkAnswer-feedback').map(text).join(' ').slice(0,300);
    return {dialog, button: buttons[0], verdict: verdicts[0].toLowerCase().replace(' ', '-'), feedback};
  }
  function marks(job, raw, el, dialog) {
    const f = L(), c = f.contentOf(raw), right = [], wrong = [];
    const pool = job.kind === 'select' ? f.models(c.answers) : job.kind === 'labels' ? f.models(c.labelLocationItems) : f.models(c.labelItems);
    let unmapped = false;
    for (const node of new Set([...q(el, '.correctAnswer, .incorrectAnswer'), ...q(dialog, '.correctAnswer, .incorrectAnswer')])) {
      if (!visible(node)) continue;
      const cls = String(node.className || node.getAttribute('class') || '').split(/\s+/);
      const good = cls.includes('correctAnswer'), bad = cls.includes('incorrectAnswer');
      if (!good && !bad) continue;
      let ctx;
      try { ctx = G.ko.contextFor(node); } catch (e) { ctx = null; }
      const data = ctx ? [ctx.$data, ctx.$parent, ...(ctx.$parents || [])] : [];
      const ix = pool.findIndex(p => data.some(d => d === p || f.A(d) === f.A(p)));
      if (ix < 0 || good && bad) { unmapped = true; continue; }
      let part = String(ix);
      if (job.kind !== 'select') {
        const state = decode(job.kind, snapshot(job,raw).state);
        const p = state.find(p => p[job.kind === 'labels' ? 1 : 0] === ix);
        if (!p) { unmapped = true; continue; }
        part = p.join(':');
      }
      (good ? right : wrong).push(part);
    }
    return {right: [...new Set(right)].sort(), wrong: [...new Set(wrong)].sort(), unmapped};
  }
  function next(job, snap, right, wrong, tried, setOnly) {
    const state = decode(job.kind, snap.state), r = new Set(right), w = new Set(wrong);
    if (job.kind === 'select') {
      if (!job.multiselect) return Array.from({length:job.opt_h.length},(_,i)=>[i]).find(s => !tried.has(encode(job.kind,s))) || null;
      const good = [...r].map(Number).sort((a,b)=>a-b);
      const just = encode(job.kind,good);
      if (setOnly && good.length && !tried.has(just)) return good;
      const undecided = Array.from({length:job.opt_h.length},(_,i)=>i).find(i=>!r.has(String(i))&&!w.has(String(i)));
      return undecided == null ? null : [...good,undecided];
    }
    if (job.kind === 'categories') {
      const out = state.map(p => p.slice());
      for (const p of out) if (w.has(p.join(':'))) {
        const target = Array.from({length:job.targets},(_,i)=>(p[1]+i+1)%job.targets).find(ci=>!w.has(p[0]+':'+ci));
        if (target == null) return null;
        p[1] = target;
      }
      return out;
    }
    const bad = state.filter(p=>w.has(p.join(':'))), good = state.filter(p=>!w.has(p.join(':')));
    if (bad.length === 1) {
      const loc = bad[0][1], used = new Set(good.map(p=>p[0]));
      const li = job.pool_h.findIndex((_,i)=>!used.has(i)&&!w.has(i+':'+loc));
      return li < 0 ? null : [...good,[li,loc]];
    }
    for (let shift=1; shift<bad.length; shift++) {
      const moved=bad.map((p,i)=>[p[0],bad[(i+shift)%bad.length][1]]);
      if (moved.every(p=>!w.has(p.join(':')))) return [...good,...moved];
    }
    return null;
  }
  async function run(cfg, jobs) {
    const ck=clock(), deadline=ck.now()+CALL_DEADLINE_MS, f=L();
    const out={ok:true, module:cfg.module_id, run:cfg.run_id, page:cfg.page, policy:cfg.policy, rat_page:cfg.rat_page,
      mode:'check', observed_at:new Date(ck.now()).toISOString(), ...f.ids({}), writes:0, items:[]};
    const sw=cfg.save_wait || {}, reserve=VERDICT_MAX_MS+(cfg.dialog_max_ms??15000)+(sw.max_ms??20000);
    const enough=()=>deadline-ck.now()>=reserve;
    const cap=ms=>Math.max(0,Math.min(ms,deadline-ck.now()));
    const end=reason=>{out.fatal=reason; out.ok=false;};
    if (cfg.enabled !== true || !cfg.policy || cfg.policy.enabled !== true) {end('check-answer-off'); return out;}
    if (!G.LtGuard || G.LtGuard.version<2 || !G.LtGuard.armCheck || !G.LtGuard.commitControl) {end('guard-version'); return out;}
    if (G.document.__ltCheckUnsafe === true) {end('fresh-document-required'); return out;}
    if (!sw.calibration || f.staleReason(sw.calibration) || !G.LtNet) {end('calibration-stale'); return out;}
    const loc=f.checkLocation(cfg,null);
    if (loc.fatal) {end('forbidden-route'); return out;}
    const initialTrips=trips();
    for (const job of jobs) {
      const prior=job.resume || {};
      const row={item_id:job.item,kind:job.kind,draft_h:job.draft_h,draft_state:job.draft_state, final_h:prior.final_h||job.draft_h,
        state:prior.state??job.draft_state, stop:'not-finished',moved:false,presses_before:prior.presses_before||0,
        presses:[],feedback:'',pool_h:job.pool_h||[],tried:(prior.tried||[]).slice(),right:(prior.right||[]).slice(),wrong:(prior.wrong||[]).slice(),
        best_state:prior.best_state??job.draft_state,best_right:prior.best_right||0,best_verdict:prior.best_verdict||null,
        final_verdict:prior.final_verdict||null,wrote:false,save_signal:null};
      row.moved=row.final_h.split('/')[1]!==job.draft_h.split('/')[1];
      out.items.push(row);
      if (out.fatal || !enough()) continue;
      if (!TYPES.includes(job.type)) {row.stop='type-not-checkable'; continue;}
      if (job.attempts !== 'multi' || (cfg.single_attempt_keys||[]).includes(String(job.panel_key))) {row.stop='single-attempt'; continue;}
      if (cfg.rat_page?.check_allowed !== true || cfg.rat_page?.rat !== false || (cfg.rat_page.sources||[]).some(s=>['page-text','rat-answers'].includes(s))) {row.stop='rat-page'; continue;}
      let expected=row.state, lastSnap, lastMarks, lastVerdict, retried=!!prior.wrote;
      const tried=new Set(row.tried), right=new Set(row.right), wrong=new Set(row.wrong);
      async function writeState(state) {
        if (!enough()) {row.stop='not-finished';row.pending_state=encode(job.kind,state);return false;}
        const check=live(cfg,job,expected,retried);
        if (check.reason) {row.stop=check.reason;if(check.call)end(check.reason);return false;}
        if (trips()!==initialTrips) {row.stop='guard-trip';end('guard-trip');return false;}
        const inp=f.inputOf(check.raw), parent=f.stateModelOf(check.raw), key=f.saveKeyOf(check.raw);
        const w={net:G.LtNet,keys:new Set(),writes:0,threw:0};
        const write=(model,fn)=>f.write(w,ck,key,model,parent,fn);
        let ok=true;
        if (job.kind==='select') ok=write(inp,()=>inp.set('selectedAnswers',state.slice()));
        else {
          const target=f.addAnswerTarget(check.raw);
          if (!target) {row.stop='write-mismatch';return false;}
          const existing=decode(job.kind,check.snap.state);
          for (const [li,ti] of state) {
            if (existing.some(p=>p[0]===li&&p[1]===ti)) continue;
            if (!enough()) {row.stop='not-finished';ok=false;break;}
            if (!write(target.t,()=>job.kind==='categories'?target.t.addAnswer(li,ti,{}):target.t.addAnswer(li,ti))) {ok=false;break;}
          }
        }
        row.wrote=row.wrote||w.writes>0;out.writes+=w.writes;
        const observed=snapshot(job,check.raw);
        row.state=observed.state;row.final_h=pairHash(observed);row.final_verdict=null;
        row.moved=observed.ih!==Number(job.draft_h.split('/')[1]);
        if (!ok || observed.state!==encode(job.kind,state)) {row.stop='write-mismatch';return false;}
        expected=observed.state;retried=true;
        let wait;
        try {wait=await G.LtNet.waitForSave([key],{...sw,max_ms:cap(sw.max_ms??20000)});} catch(e) {wait={save_signal:'error'};}
        row.save_signal=wait.save_signal;out.save_signal=wait.save_signal;
        if (trips()!==initialTrips) {row.stop='guard-trip';end('guard-trip');return false;}
        if (!['model','net'].includes(wait.save_signal)) {row.stop='save-'+(wait.save_signal||'error');end(row.stop);return false;}
        const after=live(cfg,job,expected,true);
        if (after.reason) {row.stop=after.reason;if(after.call)end(after.reason);return false;}
        return true;
      }
      if (prior.pending_state != null) {
        if (!await writeState(decode(job.kind,prior.pending_state))) continue;
        if (prior.pending_stop) {row.stop=prior.pending_stop;row.final_verdict=prior.pending_verdict;continue;}
      }
      // A saved retry can span calls. Never press a state already in the record.
      while (enough()) {
        const check=live(cfg,job,expected,retried);
        if (check.reason) {row.stop=check.reason;if(check.call)end(check.reason);break;}
        if (trips()!==initialTrips) {row.stop='guard-trip';end('guard-trip');break;}
        if (tried.has(check.snap.state)) {row.stop='no-progress';break;}
        lastSnap=check.snap;
        const result=await observe(()=>trips()!==initialTrips?{reason:'guard-trip'}:readVerdict(),cap(VERDICT_MAX_MS),()=>{
          const ready=live(cfg,job,expected,retried);
          if (ready.reason) throw Object.assign(new Error(ready.reason),{checkReason:ready.reason});
          if (!G.LtGuard.armCheck(cfg.page,String(job.panel_key))) throw Error('token');
          press(check.button);
        });
        if (result?.error) {Object.defineProperty(G.document,'__ltCheckUnsafe',{value:true});row.stop=result.reason||'guard-trip';end(row.stop);break;}
        tried.add(lastSnap.state);row.tried=[...tried];
        const verdict=result?.verdict||'unread';
        const pm=result?.verdict && !(job.kind==='select' && !job.multiselect)?marks(job,check.raw,check.el,result.dialog):{right:[],wrong:[]};
        row.presses.push([lastSnap.state,verdict,pm.right.join(',')||'-',pm.wrong.join(',')||'-'].join('|'));
        row.final_verdict=verdict;row.feedback=(result?.feedback||'').slice(0,300);
        row.final_h=pairHash(snapshot(job,check.raw));row.state=snapshot(job,check.raw).state;
        row.moved=row.final_h.split('/')[1]!==job.draft_h.split('/')[1];
        // An unresolved press can still create a dialog later. Bind the refusal
        // to this document so reloading the script cannot clear that ambiguity.
        if (!result?.verdict) {Object.defineProperty(G.document,'__ltCheckUnsafe',{value:true});row.stop=result?.reason||'verdict-unread';end(row.stop);break;}
        const closed=await observe(()=>!visible(result.dialog)||!dialogs().includes(result.dialog),cap(cfg.dialog_max_ms??15000),()=>close(result.button));
        if (!closed || closed.error) {row.stop='dialog-stuck';end(row.stop);break;}
        if (trips()!==initialTrips) {row.stop='guard-trip';end(row.stop);break;}
        if (snapshot(job,check.raw).state!==lastSnap.state) {row.stop='lt-changed-state';row.final_verdict=null;break;}
        const after=live(cfg,job,expected,retried);
        if (['locked','read-only','button-disabled','no-button'].includes(after.reason)) {row.stop='locked';break;}
        if (after.reason) {row.stop=after.reason;if(after.call)end(row.stop);break;}
        if (verdict==='correct') {row.stop='correct';break;}
        if (pm.unmapped || pm.wrong.some(p=>right.has(p)) || pm.right.some(p=>wrong.has(p))) {row.stop='marks-unmapped';break;}
        lastMarks=pm;lastVerdict=verdict;
        const before=new Set([...right,...wrong]);
        pm.right.forEach(p=>right.add(p));pm.wrong.forEach(p=>wrong.add(p));
        row.right=[...right];row.wrong=[...wrong];
        // Re-score prior states against all parts already proved right. A partial
        // visible mark set must never make a restore move a known correct part.
        let best=row.best_state, score=-1;
        for (const state of tried) {
          const parts=decode(job.kind,state).map(p=>Array.isArray(p)?p.join(':'):String(p));
          const count=parts.filter(p=>right.has(p)).length;
          if (count>score) {best=state;score=count;}
        }
        const known=row.presses.concat(prior.presses||[]).find(p=>p.split('|')[0]===best);
        row.best_verdict=known?known.split('|')[1]:best===row.best_state?row.best_verdict:null;
        row.best_state=best;row.best_right=score;
        const setOnly=job.multiselect && pm.wrong.length>0;
        const justRight=job.multiselect && lastSnap.state===encode('select',[...right].map(Number)) && !row.presses.slice(0,-1).some(p=>p.split('|')[0]===lastSnap.state);
        const added=job.kind==='select'&&!job.multiselect || [...right,...wrong].some(p=>!before.has(p));
        if (job.kind==='select'&&job.multiselect&&!pm.right.length&&!pm.wrong.length) row.stop='no-part-marks';
        else if (!added && !justRight) row.stop='no-progress';
        else if (job.kind!=='select' && !pm.wrong.length) row.stop='no-progress';
        else {
          const candidate=next(job,lastSnap,[...right],[...wrong],tried,setOnly);
          if (!candidate) row.stop=job.kind==='select'&&!job.multiselect?'options-exhausted':'no-next-attempt';
          else if (tried.has(encode(job.kind,candidate))) row.stop='no-progress';
          else {
            if (!await writeState(candidate)) break;
            if (!enough()) {row.stop='not-finished';break;}
            continue;
          }
        }
        if (['options-exhausted','no-next-attempt','no-part-marks','no-progress'].includes(row.stop) && lastSnap && lastMarks && lastVerdict) {
          const best=job.kind==='select'&&!job.multiselect?job.draft_state:row.best_state;
          const stop=row.stop;
          if (best!==expected && !enough()) {row.pending_state=best;row.pending_stop=stop;row.pending_verdict=row.best_verdict;row.stop='not-finished';break;}
          if (best!==expected && await writeState(decode(job.kind,best))) {row.stop=stop;row.final_verdict=best===job.draft_state?(row.presses.find(p=>p.split('|')[0]===best)?.split('|')[1]||null):row.best_verdict;}
        }
        break;
      }
    }
    if (cfg.resumed && !out.items.some(r=>r.presses.length||r.stop!=='not-finished')) out.items.forEach(r=>{r.stop='no-progress';});
    out.guard_trips=trips();
    return out;
  }
  async function verify(cfg, pages) {
    const f=L(), root=f.rootOf(), items=[];
    const loc=f.checkLocation(cfg,null);
    if (loc.fatal || !root) return {ok:false,fatal:loc.fatal||'no-lt-client'};
    const fresh=typeof G.__ltFillLastWrite!=='number' && !G.__ltCheckDocument;
    for (const [index,jobs] of Object.entries(pages)) {
      const p=f.pageAt(f.flatPages(root),Number(index));
      const by=p?f.panelsOf(p):{};
      for (const job of jobs) {
        const raw=by[String(job.panel_key)], s=raw?snapshot(job,raw):null;
        items.push([job.item,s&&shape(job,raw)&&pairHash(s)===job.final_h?'ok':raw?'BAD':'no-panel',0,s?pairHash(s):'no-panel']);
      }
    }
    return {ok:true,module:cfg.module_id,mode:'check-verify',fresh_load:fresh,...f.ids({}),observed_at:new Date(clock().now()).toISOString(),items};
  }
  G.LtCheck={run:async (cfg,jobs)=>{const r=await run(cfg,jobs);G.__ltCheckDocument=true;return r;},verify,snapshot,encode,decode,DENY};
})(typeof globalThis !== 'undefined' ? globalThis : this);
return await LtCheck.run(__LT_ARGS__.cfg, __LT_ARGS__.jobs);
