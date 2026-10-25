# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""File-only reconciliation contracts. Never reads sheets or revises cited drafts."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

NEEDS = ['schemas/reconciled.schema.json']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def covered(report):
    """Only Correct, text-changed items confirmed by the post-check hash pair."""
    out={}
    for item in report.get('items',[]):
        c=item.get('check_answer') or {}
        final=c.get('final') or {};draft=c.get('draft') or {};r=item.get('receipt') or {}
        if (final.get('verdict')=='correct' and item.get('verified') is True and
            final.get('hash')==r.get('expected_hash')==r.get('observed_hash') and
            '/' in final.get('hash','') and final['hash'].split('/')[0]!=draft.get('hash','').split('/')[0]):
            out[item['item_id']]=item
    return out


def _path(pin, base):
    path=Path(pin['path'])
    return path if path.is_absolute() else base/path


def _read_pin(pin, base, roots=()):
    from ltcore.rat import within
    path=_path(pin,base)
    if any(within(path,r) for r in roots):
        raise ValueError('source pin enters a RAT root')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=pin['sha256']:
        raise ValueError('stale source pin')
    return json.loads(raw),path


def worklist(module, answers, report):
    drafts={a['item_id']:a for a in answers['items']}
    return {'schema_version':1,'module_id':module['module_id'],'items':[
        {'item_id':iid,'draft_state':copy.deepcopy(i['check_answer']['draft']),
         'draft_segments':copy.deepcopy(drafts[iid].get('segments',[])),
         'draft_provenance':copy.deepcopy(drafts[iid].get('provenance',[])),
         'lt_state':copy.deepcopy(i['check_answer']['final']),
         'presses':copy.deepcopy(i['check_answer']['presses']),
         'lt_feedback':i['check_answer'].get('lt_feedback',''),'feedback_is_untrusted_data':True}
        for iid,i in covered(report).items()]}


def rails_reconciled(doc, ctx):
    from ltcore import validate as v
    errors=[]
    def fail(rail, detail): errors.append(f'{rail}: {detail}')
    try:
        answers,apath=_read_pin(doc['source_answers'],ctx.base_dir,ctx.rat_roots)
        report,fpath=_read_pin(doc['source_fill_report'],ctx.base_dir,ctx.rat_roots)
        if report['source_answers']['sha256']!=doc['source_answers']['sha256'] or any(x.get('module_id')!=doc['module_id'] for x in (answers,report)):
            raise ValueError('answers/report binding differs')
        if ctx.module and ctx.module['module_id']!=doc['module_id']:
            raise ValueError('module binding differs')
    except (OSError,ValueError,KeyError,TypeError) as exc:
        fail('reconciled-binding',str(exc));return errors
    wanted=covered(report); entries={i['item_id']:i for i in doc['items']}
    if set(entries)!=set(wanted) or len(entries)!=len(doc['items']):
        fail('reconciled-coverage','entries must equal verified Correct text changes')
    drafts={i['item_id']:i for i in answers['items']}
    try:
        ledger,_=_read_pin(report['source_check_answer'],fpath.parent,ctx.rat_roots)
    except (OSError,ValueError,KeyError,TypeError):
        ledger={};fail('reconciled-provenance','missing or stale check ledger pin')
    ledger_presses={}
    for call in ledger.get('checks',[]):
        for row in call.get('items',[]):
            for n,press in enumerate(row.get('presses',[]),row.get('presses_before',0)+1):
                ledger_presses[(row['item_id'],call['receipt_sha256']+'#'+str(n))]=press
    schema=v._load_schema(v.SCHEMAS_DIR,'answers.schema.json')['$defs']['answer']['properties']['provenance']['items']
    for iid,entry in entries.items():
        if iid not in wanted or iid not in drafts:
            continue
        c=wanted[iid]['check_answer'];draft=drafts[iid]
        expected_lt=dict(c['final']['state'],hash=c['final']['hash'],presses=len(c['presses']))
        expected_draft=dict(c['draft']['state'],hash=c['draft']['hash'])
        if entry['lt_state']!=expected_lt or entry['draft_state']!=expected_draft:
            fail('reconciled-state',iid)
        seen=[];ds=draft.get('segments',[])
        dp={p['ref']:p for p in draft.get('provenance',[])};rp={p['ref']:p for p in entry['provenance']}
        if set(dp)&set(rp) or len(rp)!=len(entry['provenance']):
            fail('reconciled-provenance',iid+' duplicate ref')
        used=set();lt_count=0
        for seg in entry['segments']:
            origin=seg['origin'];refs=seg.get('provenance_refs',[])
            if origin=='draft':
                index=seg.get('draft_segment');seen.append(index)
                original=ds[index] if type(index) is int and 0<=index<len(ds) else None
                if original is None or {k:value for k,value in seg.items() if k not in {'origin','draft_segment'}}!=original:
                    fail('reconciled-draft-verbatim',iid)
                sources=dp
            elif origin=='research':
                sources=rp;used.update(refs)
            elif origin=='lt':
                lt_count+=1
                press=ledger_presses.get((iid,seg.get('lt_press')),'')
                parts=press.split('|')
                final_press=next((p for p in c['presses'] if p['receipt_sha256']+'#'+str(p['n'])==seg.get('lt_press')),None)
                if refs or 'provenance_refs' in seg or len(parts)!=4 or parts[1]!='correct' or not final_press or final_press['hash']!=c['final']['hash']:
                    fail('reconciled-provenance',iid+' Lt press must resolve to the accepted final state')
                continue
            else:
                if seg['kind']!='connective' or refs or len(seg['text'])>120:
                    fail('reconciled-provenance',iid+' connective introduces no facts')
                continue
            if seg['kind']=='claim' and not refs or any(ref not in sources for ref in refs):
                fail('reconciled-provenance',iid+' unresolved claim')
        if lt_count!=1:
            fail('reconciled-provenance',iid+' needs exactly one Lt segment')
        for drop in entry['dropped']:
            seen.append(drop['draft_segment'])
            if not drop['reason'].strip():
                fail('reconciled-draft-verbatim',iid+' missing drop reason')
        if sorted(seen,key=lambda x:-1 if x is None else x)!=list(range(len(ds))):
            fail('reconciled-draft-verbatim',iid+' each draft segment must occur once')
        for ref,p in rp.items():
            if v._schema_errors(schema,p) or ref not in used or p['tier']==4 and not (v.DOI_RE.match(p['source_id']) or v.PMID_RE.match(p['source_id'])):
                fail('reconciled-provenance',iid+' invalid research source '+ref)
            if v.QUARANTINED_SOURCE_RE.search(p['source_id']) or v.QUARANTINED_SOURCE_RE.search(p['locator']):
                fail('reconciled-provenance',iid+' quarantined reference')
            if not ctx.model_answer_read and p.get('source_id')==v.MODEL_ANSWER_SOURCE:
                fail('reconciled-provenance',iid+' model-answer-policy')
            if any(v._rat_source(p.get(k,''),ctx) for k in ('source_id','locator')):
                fail('reconciled-provenance',iid+' rat-root-quarantine')
        if rp:
            deepest=max(p['tier'] for p in rp.values());descents={(d['from_tier'],d['to_tier']) for d in entry.get('tier_descent',[])}
            if any((t,t+1) not in descents for t in range(1,deepest)):
                fail('reconciled-provenance',iid+' missing tier descent')
        sources=dict(dp,**rp)
        for conflict in entry.get('conflicts',[]):
            high=sources.get(conflict['higher_ref']);low=sources.get(conflict['lower_ref'])
            if not high or not low or high['tier']>=low['tier']:
                fail('reconciled-provenance',iid+' tier conflict refs')
        dispute=entry.get('dispute')
        if entry['outcome']=='combined':
            if dispute is not None: fail('reconciled-dispute',iid+' combined carries no dispute')
        elif not dispute or not dispute.get('evidence'):
            fail('reconciled-dispute',iid+' source evidence required')
        else:
            for ev in dispute['evidence']:
                source=sources.get(ev['ref'])
                if not source or not 1<=len(ev['quote'])<=300 or not ev['quote'].strip() or not ev['contradiction'].strip() or ev['locator']!=source.get('locator'):
                    fail('reconciled-dispute',iid+' evidence needs a resolving source and locator')
        # Feedback alone is no source. Human research decides whether a source actually contradicts Lt.
    return errors


def rebind_document(doc, previous, final):
    """Permit only RAT item additions and report bookkeeping to change."""
    if previous.get('source_answers')!=final.get('source_answers'):
        raise ValueError('answers pin changed')
    if previous.get('check_answer')!=final.get('check_answer') or previous.get('source_check_answer')!=final.get('source_check_answer'):
        raise ValueError('check summary or ledger changed')
    if covered(previous)!=covered(final):
        raise ValueError('reconciliation coverage or accepted evidence changed')
    rat_pages={r['page_index'] for r in final.get('rat_pages',[]) if r.get('rat') is True}
    rat_ids={i['item_id'] for i in final['items'] if i.get('answer_source',{}).get('kind')=='rat-sheet'}
    before={i['item_id']:i for i in previous['items']};after={i['item_id']:i for i in final['items']}
    if set(before)!=set(after):raise ValueError('report item inventory changed')
    if any(before[i]!=after[i] for i in before if i not in rat_ids):
        raise ValueError('non-RAT items changed')
    if any(i in covered(previous) or after[i].get('check_answer') for i in rat_ids):
        raise ValueError('RAT fill changes checked items')
    bookkeeping={'items','rat_pages','pages_filled','source_receipts','source_page_kinds','ended_at','save_waits'}
    stable=(set(previous)|set(final))-bookkeeping
    if any(previous.get(k)!=final.get(k) for k in stable):
        raise ValueError('report boundary or identity changed')
    old_pages={r['page_index']:r for r in previous.get('rat_pages',[])}
    if not set(old_pages)-rat_pages <= {r['page_index'] for r in final.get('rat_pages',[])}:
        raise ValueError('non-RAT page summary removed')
    for row in final.get('rat_pages',[]):
        if row['page_index'] not in rat_pages and row!=old_pages.get(row['page_index']):
            raise ValueError('non-RAT page summary changed')
    return copy.deepcopy(doc)


def atomic_json(path, doc):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,scratch=tempfile.mkstemp(prefix='.reconcile-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8', newline="\n") as f:
            json.dump(doc,f,indent=1,ensure_ascii=False);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(scratch,path)
    finally:
        if os.path.exists(scratch):os.unlink(scratch)
