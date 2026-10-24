# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline Check Answer eligibility, index states and ledger projection.

This module never scans or opens RAT sheets. Callers supply J3's fresh status.
"""
from __future__ import annotations

import copy
import hashlib
import html
import re

NEEDS = []
CHECK_TYPES = frozenset({'multiple-choice', 'image-labeling', 'drag-and-drop-labeling', 'categorization'})
VERDICT_MAX_MS = 10000
CALL_DEADLINE_MS = 90000


def djb2(text):
    value = 5381
    raw = str(text).encode('utf-16-le', 'surrogatepass')
    for i in range(0, len(raw), 2):
        value = (value * 33 + int.from_bytes(raw[i:i+2], 'little')) & 0xffffffff
    return value


def plain(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]*>', '', str(text)))).strip()


def decode(kind, state):
    if not state:
        return []
    if kind == 'select':
        return [int(i) for i in state.split(',')]
    return [[int(i) for i in p.split(':')] for p in state.split(',')]


def encode(kind, state):
    return ','.join(map(str, sorted(state))) if kind == 'select' else ','.join(':'.join(map(str,p)) for p in sorted(state))


def hashes(kind, state, pool):
    parts = decode(kind, state)
    if kind == 'select':
        canon = 'sel:' + encode(kind, parts)
        return f'{djb2(canon)}/{djb2(canon)}'
    prefix = 'lab' if kind == 'labels' else 'cat'
    lines = sorted((f'{target}\t{plain(pool[label])}' for label,target in parts), key=lambda v:v.encode('utf-16-be'))
    indices = sorted(f'{target}\t{label}' for label,target in parts)
    return f'{djb2(prefix+":"+chr(10).join(lines))}/{djb2(prefix+"i:"+chr(10).join(indices))}'


def classification_pin(page):
    """Pin captured author text and page-wide attempt settings, excluding learner state."""
    from ltcore.digest import normalise_text
    # Older incomplete captures cannot supply a live binding. Emit a missing pin
    # so the browser refuses and requests recapture, rather than inferring one.
    if any(not isinstance(p.get(k), str) or p.get(k) == '__proto__' for p in [*(page.get('content') or []), *(page.get('items') or [])]
           for k in ('panel_key', 'plugin_key')):
        return None
    return {
        'title': normalise_text(page.get('title') or ''),
        'report': bool(page.get('is_report_page')),
        'checkpoint': bool(page.get('is_checkpoint')),
        'content': {str(c['panel_key']): {
            'plugin_key': c.get('plugin_key'), 'text': normalise_text(c.get('text') or '')}
            for c in page.get('content') or []},
        'items': {str(i['panel_key']): {
            'plugin_key': i.get('plugin_key'), 'attempts': i.get('attempts'),
            'assessment_type': i.get('assessment_type')}
            for i in page.get('items') or []},
    }


def eligibility(item, page, plan, status, *, written=False, verified=False, calibrated=False):
    """S2-S10. Return the first static refusal, or None. S1 is command-level."""
    iid = item['item_id']
    singles = (plan.get('boundary_map') or {}).get('single_attempts_passed') or []
    if item.get('attempts') != 'multi' or iid in plan.get('single_attempt_items', []) or any(s.get('item_id')==iid for s in singles):
        return 'single-attempt'
    if item.get('assessment_type') != 'instantFeedback':
        return 'assessment-' + str(item.get('assessment_type'))
    if item.get('type') not in CHECK_TYPES:
        return 'type-not-checkable'
    if status.get('check_allowed') is not True or status.get('rat') is not False:
        return status.get('check_reason') or ('rat-page' if status.get('rat') is True else 'rat-unmarked')
    if set(status.get('sources', [])) & {'page-text','rat-answers'}:
        return 'rat-page'
    if item.get('parent_item_id') is not None:
        return 'in-container'
    if item.get('commit_state') != 'open' or item.get('read_only') is not False:
        return 'read-only-or-unknown'
    idx = page['page_index']
    if (plan.get('stop_before_page') is not None and idx >= plan['stop_before_page'] or
        idx in plan.get('report_pages', []) or idx in plan.get('checkpoint_pages', []) or
        idx not in {p['page_index'] for p in plan.get('pages', [])}):
        return 'page-not-allowed'
    if not written:
        return 'draft-not-written'
    if not verified:
        return 'draft-not-verified'
    if not calibrated:
        return 'origin-uncalibrated'
    return None


def pool_for(item):
    constraints = item.get('constraints') or {}
    return [o['text'] for o in item.get('options', [])]


def ordered_pool(item, pool_h):
    """Recover fill-time order from hashes, without exposing a key in the capture.

    Capture deliberately sorts label pools. Distinct labels with colliding hashes
    refuse instead of inventing an identity. Duplicate equal labels retain indices.
    """
    pool=pool_for(item)
    mapping={}
    for label in pool:
        h=djb2(plain(label))
        if h in mapping and mapping[h]!=label:
            raise ValueError('ambiguous label hash')
        mapping[h]=label
    if sorted(pool_h)!=sorted(djb2(plain(label)) for label in pool):
        raise ValueError('label pool differs from capture')
    return [mapping[h] for h in pool_h]


def target_count(item):
    c = item.get('constraints') or {}
    if item.get('type') == 'categorization':
        return len(c.get('categories') or [])
    m = re.search(r'\blocations=(\d+)', c.get('note') or '')
    return int(m.group(1)) if m else 0


def multiselect(item):
    return (item.get('constraints') or {}).get('note') == 'multiselect'


def state_text(item, kind, encoded, targets=None, *, pool=None):
    """Map indices offline to capture text; never use a sheet or live answer text."""
    values = decode(kind, encoded)
    if kind == 'select':
        return {'proposed_selection': [item['options'][i]['option_id'] for i in values]}
    pool = pool_for(item) if pool is None else pool
    bins = (item.get('constraints') or {}).get('categories') or []
    return {'label_placements': [{'label':pool[li], 'target':str((targets or bins)[ti]) if (targets or bins) else f'anchor {ti}', 'target_index':ti} for li,ti in values]}


def ledger_items(ledger):
    grouped = {}
    for call in (ledger or {}).get('checks', []):
        for row in call.get('items', []):
            grouped.setdefault(row['item_id'], []).append((call,row))
    return grouped


def project(module, answers, ledger):
    """Preserve recorded policy, cumulative presses and exact state identity in reports."""
    caps = {i['item_id']:i for p in module['pages'] for i in p['items']}
    drafts = {i['item_id']:i for i in answers['items']}
    result = {}
    for iid, calls in ledger_items(ledger).items():
        cap, draft = caps[iid], drafts[iid]
        presses, evidence = [], []
        first, last = calls[0][1], calls[-1][1]
        pool=ordered_pool(cap,first['pool_h']) if first['kind']!='select' else pool_for(cap)
        for call,row in calls:
            evidence.append({k:copy.deepcopy(call.get(k)) for k in ('receipt_sha256','policy','rat_page','page')})
            evidence[-1].update(order=call['items'].index(row), presses=len(row['presses']), final_hash=row['final_h'], stop=row['stop'], wrote=bool(row.get('wrote')))
            for n, encoded in enumerate(row['presses'],row.get('presses_before',0)+1):
                state, verdict, right, wrong = encoded.split('|')
                presses.append({'n':n, 'state':state_text(cap,row['kind'],state,pool=pool),
                    'index_state':state, 'hash':hashes(row['kind'],state,pool), 'verdict':verdict,
                    'right':[] if right=='-' else right.split(','), 'wrong':[] if wrong=='-' else wrong.split(','),
                    'receipt_sha256':call['receipt_sha256']})
        result[iid]={'eligible':bool(presses) or last['stop']=='not-finished','reason':None if presses or last['stop']=='not-finished' else last['stop'],'policy':copy.deepcopy(calls[0][0].get('policy')),
            'rat_page':copy.deepcopy(calls[0][0].get('rat_page')), 'calls':evidence, 'pool_h':first['pool_h'],
            'draft':{'state':state_text(cap,first['kind'],first['draft_state'],pool=pool),
                     'hash':first['draft_h'],'refs':sorted({r for s in draft.get('segments',[]) for r in s.get('provenance_refs',[])})},
            'presses':presses,
            'final':{'state':state_text(cap,last['kind'],last['state'],pool=pool), 'hash':last['final_h'],
                     'index_state':last['state'], 'verdict':last.get('final_verdict'), 'stop':last['stop'], 'moved':last['moved']},
            'lt_feedback':last.get('feedback','')[:300]}
    return result


def summary(items):
    entries = [i['check_answer'] for i in items if 'check_answer' in i]
    pressed = [e for e in entries if e['presses']]
    ineligible = {}
    for e in entries:
        if not e['eligible']:
            ineligible[e['reason']] = ineligible.get(e['reason'],0)+1
    return {'policy':[c['policy'] for e in entries for c in e.get('calls',[])],
            'presses':sum(len(e['presses']) for e in entries),'items_pressed':len(pressed),
            'correct':sum(e['final']['verdict']=='correct' for e in pressed),
            'moved':sum(e['final']['moved'] for e in pressed),
            'not_correct':sum(e['final']['verdict']!='correct' for e in pressed),'ineligible':ineligible}


def rails(doc, module, *, policy=None):
    """Check recorded evidence independently of today's config and page-kinds store."""
    errors=[]
    def fail(rail,iid):
        errors.append(f'{rail}: {iid}')
    captures={i['item_id']:(p,i) for p in (module or {}).get('pages',[]) for i in p['items']}
    recorded={r['page_index']:r for r in doc.get('rat_pages',[])}
    singles=set((module or {}).get('commit_boundaries',{}).get('single_attempt_items',[]))
    for item in doc.get('items',[]):
        c=item.get('check_answer')
        if not c or not c['presses']:
            continue
        iid=item['item_id']; page,cap=captures.get(iid,({},{})); presses=c['presses']
        if not c.get('calls') or c.get('policy',{}).get('enabled') is not True or any((x.get('policy') or {}).get('enabled') is not True for x in c.get('calls',[]) if x.get('presses')):
            fail('check-answer-policy',iid)
        if cap.get('attempts',item.get('attempts'))=='single' or iid in singles:
            fail('check-answer-single',iid)
        if cap.get('type',item.get('item_type')) not in CHECK_TYPES or cap.get('assessment_type')!='instantFeedback' or cap.get('parent_item_id') is not None:
            fail('check-answer-scope',iid)
        for call in c.get('calls',[]):
            status=call.get('rat_page') or {}
            source=set(status.get('sources') or [])
            actual=[p for p in presses if p['receipt_sha256']==call['receipt_sha256']]
            if len(actual)!=call['presses']:
                fail('check-answer-provenance',iid)
            if actual and (call['page']!=page.get('page_index') or status.get('page_index')!=page.get('page_index')):
                fail('check-answer-scope',iid)
            rp=recorded.get(page.get('page_index'),{})
            if call.get('presses') and (status.get('rat') is not False or status.get('check_allowed') is not True or source&{'page-text','rat-answers'} or
                rp and (rp.get('rat') is not False or rp.get('check_allowed') is not True) or set(rp.get('sources',[]))&{'page-text','rat-answers'}):
                fail('check-answer-scope',iid)
        if c.get('calls') and (c['rat_page']!=c['calls'][0]['rat_page'] or c['policy']!=c['calls'][0]['policy']):
            fail('check-answer-provenance',iid)
        if any(p['receipt_sha256'] not in {call['receipt_sha256'] for call in c.get('calls',[])} for p in presses):
            fail('check-answer-policy',iid)
        # Candidate status must be explicit. Non-candidates also echo their safe status.
        from ltcore import page_kinds
        if cap and page_kinds.is_candidate(page,policy) and not (c.get('rat_page') or {}).get('candidate'):
            fail('check-answer-scope',iid)
        kind='select' if cap.get('type')=='multiple-choice' else 'categories' if cap.get('type')=='categorization' else 'labels'
        try:
            pool=ordered_pool(cap,c.get('pool_h',[])) if kind!='select' else pool_for(cap)
        except (ValueError,TypeError):
            fail('check-answer-provenance',iid);continue
        n=len(cap.get('options',[])) if kind=='select' else len(pool)
        targets=target_count(cap)
        multi=multiselect(cap)
        limit=(2*n+1 if multi else n) if kind=='select' else n*targets+1
        if len(presses)>limit:
            fail('check-answer-bound',iid)
        expected_numbers=list(range(1,len(presses)+1))
        if [p['n'] for p in presses]!=expected_numbers:
            fail('check-answer-bound',iid)
        seen=set(); decided=set(); wrong=set(); right=set(); unread_call=None
        for j,p in enumerate(presses):
            state=p['index_state']
            try:
                vals=decode(kind,state)
                shape_ok=(len(vals)==len(set(vals)) and all(0<=x<n for x in vals)) if kind=='select' else (all(len(p)==2 and 0<=p[0]<n and 0<=p[1]<targets for p in vals) and len({p[0] for p in vals})==len(vals))
                if not shape_ok or kind=='select' and not multi and len(vals)!=1:raise ValueError('index shape')
                if p['state']!=state_text(cap,kind,state,pool=pool):
                    fail('check-answer-provenance',iid)
            except (ValueError,TypeError,IndexError):
                fail('check-answer-bound',iid);continue
            if p['hash']!=hashes(kind,state,pool):
                fail('check-answer-provenance',iid)
            if state in seen or (set(map(str,vals)) if kind=='select' else {':'.join(map(str,v)) for v in vals}) & wrong:
                fail('check-answer-bound',iid)
            if unread_call==p['receipt_sha256']:
                fail('check-answer-unread-write',iid)
            if p['verdict']=='unread':
                unread_call=p['receipt_sha256']
                call=next((x for x in c.get('calls',[]) if x.get('receipt_sha256')==unread_call),{})
                if call.get('final_hash')!=p['hash']:
                    fail('check-answer-unread-write',iid)
            current_parts=set(map(str,vals)) if kind=='select' else {':'.join(map(str,v)) for v in vals}
            if not right <= current_parts or set(p['wrong']) & right or set(p['right']) & wrong:
                fail('check-answer-bound',iid)
            parts=set(p['right']+p['wrong'])
            if kind=='select' and not multi:
                parts.update(map(str,vals))
            right.update(p['right']); wrong.update(p['wrong'])
            exception=kind=='select' and multi and set(map(str,vals))==right and bool(right) and state not in seen
            if p['verdict'] not in {'correct','unread'} and not parts-decided and not exception and j<len(presses)-1:
                fail('check-answer-bound',iid)
            decided.update(parts);seen.add(state)
        final=c['final']
        try:
            if final['hash']!=hashes(kind,final['index_state'],pool) or final['state']!=state_text(cap,kind,final['index_state'],pool=pool):
                fail('check-answer-provenance',iid)
            if final['moved']!=(final['hash'].split('/')[1]!=c['draft']['hash'].split('/')[1]):
                fail('check-answer-provenance',iid)
        except (ValueError,TypeError,IndexError):
            fail('check-answer-provenance',iid)
        if c['final']['moved']:
            if item.get('receipt',{}).get('expected_hash')!=c['final']['hash'] or item.get('lt_conflict'):
                fail('check-answer-provenance',iid)
        if c['draft']['hash'] != presses[0]['hash'] or c['draft']['state']!=presses[0]['state']:
            fail('check-answer-provenance',iid)
    # Report items follow answers.json; browser calls follow capture order.
    # Preserve the receipt's item position before judging cross-item effects.
    by_call={}
    for item in doc.get('items',[]):
        c=item.get('check_answer') or {}
        for call in c.get('calls',[]):
            by_call.setdefault(call['receipt_sha256'],[]).append((call.get('order',0),item['item_id'],c,call))
    for receipt,events in by_call.items():
        if len({e[0] for e in events})!=len(events):
            fail('check-answer-provenance','duplicate browser item order')
        unread=False
        for _,iid,c,call in sorted(events,key=lambda event:event[0]):
            if unread and (call.get('presses') or call.get('wrote')):
                fail('check-answer-unread-write',iid)
            if any(p['verdict']=='unread' and p['receipt_sha256']==receipt for p in c.get('presses',[])):
                unread=True
    if doc.get('check_answer') and doc['check_answer']!=summary(doc['items']):
        fail('check-answer-provenance','summary does not match item evidence')
    return errors
