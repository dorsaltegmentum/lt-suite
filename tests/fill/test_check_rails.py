# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Historical check rails reject forged policy, scope, repeats and provenance (55,72)."""
import copy,json
from pathlib import Path
import pytest
from ltcore import check_answer as ca,validate
FIX=Path(__file__).resolve().parents[1]/'fixtures'
M=FIX/'modules/module-c1-r108.json';F=FIX/'fill-reports/fill-report-c1-r108.json'


def docs():return json.loads(F.read_text()),json.loads(M.read_text())


@pytest.mark.parametrize('mutation,rail',[
 (lambda d,m:d['items'][0]['check_answer']['policy'].update(enabled=False),'check-answer-policy'),
 (lambda d,m:m['pages'][0]['items'][0].update(attempts='single'),'check-answer-single'),
 (lambda d,m:m['pages'][0]['items'][0].update(assessment_type='graded'),'check-answer-scope'),
 (lambda d,m:d['items'][0]['check_answer']['calls'][0]['rat_page'].update(rat=None),'check-answer-scope'),
 (lambda d,m:d['items'][0]['check_answer']['calls'][0]['rat_page'].update(rat=True),'check-answer-scope'),
 (lambda d,m:d['items'][0]['check_answer']['calls'][0]['rat_page'].update(sources=['user-mark','rat-answers']),'check-answer-scope'),
 (lambda d,m:d['items'][0]['check_answer']['presses'].append(copy.deepcopy(d['items'][0]['check_answer']['presses'][0])),'check-answer-bound'),
 (lambda d,m:d['items'][0]['receipt'].update(expected_hash='wrong'),'check-answer-provenance'),
 (lambda d,m:d['items'][0].update(lt_conflict={'held_answer':'A','lt_indicated':'B'}),'check-answer-provenance'),
 (lambda d,m:d['items'][0]['check_answer']['presses'][0].update(verdict='unread'),'check-answer-unread-write'),
])
def test_each_check_rail(mutation,rail):
    doc,mod=docs();mutation(doc,mod)
    errors=ca.rails(doc,mod)
    assert any(rail in e for e in errors),errors


def test_recorded_status_is_independent_of_live_store(tmp_path):
    doc,mod=docs();assert ca.rails(doc,mod)==[]
    # A new classification elsewhere cannot change the policy that authorized these presses.
    (tmp_path/'page-kinds.json').write_text('{"marks":[]}')
    assert ca.rails(doc,mod)==[]


def test_report_projection_survives_current_setting_off(emit_mod,tmp_path):
    import make_fill_report
    from ltcore import config,page_kinds,rat
    mod=json.loads(M.read_text());ans=json.loads((FIX/'answers/answers-c1-r108.json').read_text());source=json.loads(F.read_text())
    ledger=json.loads((FIX/'runs/c1-r108/check-answer.json').read_text())
    receipts={'receipts':[dict(i['receipt'],item_id=i['item_id']) for i in source['items'] if i.get('receipt')]}
    notes={'approval_mode':'manual','started_at':'2000-01-01T00:00:00Z','ended_at':'2000-01-01T00:00:00Z'}
    cfg=config.load()
    # Refresh the synthetic record under the effective policy, including its week pattern.
    import hashlib
    record=page_kinds.classify(mod,rat.policy_from_config(cfg),answers=ans,
        record=json.loads((FIX/'page-kinds/page-kinds-c1-r108.json').read_text()),
        answers_sha256=hashlib.sha256((FIX/'answers/answers-c1-r108.json').read_bytes()).hexdigest())
    pin=tmp_path/'page-kinds.json';page_kinds.atomic_write(pin,record)
    report,_=make_fill_report.build(mod,ans,receipts,notes,FIX/'answers/answers-c1-r108.json',None,
        cfg=cfg,check_ledger=ledger,page_kinds_path=pin)
    assert report['check_answer']['presses']==source['check_answer']['presses']
    first=report['items'][0]
    assert first['check_answer']['draft']['refs']==['p1']
    assert first['receipt']['expected_hash']==first['check_answer']['final']['hash']


def test_unknown_report_page_status_cannot_be_overridden_by_call():
    doc,mod=docs();next(r for r in doc['rat_pages'] if r['page_index']==3)['rat']=None
    assert any('check-answer-scope' in e for e in ca.rails(doc,mod))


def test_call_count_cannot_hide_a_press():
    doc,mod=docs();doc['items'][0]['check_answer']['calls'][0]['presses']=0
    assert any('check-answer-provenance' in e for e in ca.rails(doc,mod))


def test_unread_rail_uses_browser_order_when_answer_items_are_reordered():
    doc,mod=docs();first,unread=doc['items'][:2]
    first['check_answer']['calls'][0]['order']=0
    c=unread['check_answer'];c['calls'][0]['order']=1
    c['presses'][-1].update(verdict='unread',right=[],wrong=[])
    c['final'].update(verdict='unread',stop='verdict-unread');c['calls'][0]['stop']='verdict-unread'
    doc['items']=[unread,first];doc['check_answer']=ca.summary(doc['items'])
    assert ca.rails(doc,mod)==[]
    first['check_answer']['calls'][0]['order']=2
    assert any('check-answer-unread-write' in e for e in ca.rails(doc,mod))


def test_check_summary_preserves_informed_fill_classification(tmp_path):
    # An empty check summary must not erase the user's informed ordinary-fill mark.
    from ltcore import page_kinds,rat
    doc,mod=docs()
    for item in doc['items']:item.pop('check_answer',None)
    doc.pop('check_answer');doc.pop('source_check_answer',None)
    mod['pages'][0]['content']=[{'text':'iRAT terminology appears in this ordinary exercise.'}]
    ans=json.loads((FIX/'answers/answers-c1-r108.json').read_text())
    record=page_kinds.classify(mod,rat.DEFAULT_POLICY,answers=ans)
    record=page_kinds.mark(mod,record,1,'not-rat','This page is an ordinary exercise.')
    path=tmp_path/'page-kinds.json';page_kinds.atomic_write(path,record)
    doc['source_page_kinds']={'path':str(path),'sha256':page_kinds.record_hash(record)}
    before=validate.validate_doc(doc,'fill-report',module=mod,base_dir=F.parent)
    assert before.exit_code==0,(before.fatal,before.schema_errors,before.rail_errors)
    doc['check_answer']=ca.summary(doc['items'])
    after=validate.validate_doc(doc,'fill-report',module=mod,base_dir=F.parent)
    assert after.exit_code==0,(after.fatal,after.schema_errors,after.rail_errors)
    doc['source_page_kinds']['sha256']='0'*64
    stale=validate.validate_doc(doc,'fill-report',module=mod,base_dir=F.parent)
    assert stale.exit_code==1 and any('rat-irat-drafted' in e for e in stale.rail_errors)


def test_historical_check_scope_survives_unneeded_classification_pin_change(tmp_path):
    doc,mod=docs()
    path=tmp_path/'page-kinds.json';path.write_text('{"marks":[]}')
    doc['source_page_kinds']={'path':str(path),'sha256':'0'*64}
    result=validate.validate_doc(doc,'fill-report',module=mod,base_dir=F.parent)
    assert result.exit_code==0,(result.fatal,result.schema_errors,result.rail_errors)


def test_check_scope_uses_effective_rat_markers(tmp_path):
    # The report rail must agree with the policy used to authorize these checks.
    doc,mod=docs()
    mod['pages'][0]['content']=[{'text':'iRAT terminology in an ordinary exercise.'}]
    cfg=tmp_path/'lt-suite.toml'
    cfg.write_text('[rat.kind_markers]\nirat="CUSTOM_INDIVIDUAL"\ntrat="CUSTOM_TEAM"\n')
    result=validate.validate_doc(doc,'fill-report',module=mod,base_dir=F.parent,config_path=cfg)
    assert result.exit_code==0,(result.fatal,result.schema_errors,result.rail_errors)
