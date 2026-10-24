# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synthetic reconciliation binding, completeness and provenance (75-80,86-87)."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from ltcore import reconcile, validate

FIX=Path(__file__).resolve().parents[1]/'fixtures'
R=FIX/'runs/c1-r108/reconciled.json'
M=FIX/'modules/module-c1-r108.json'
A=FIX/'answers/answers-c1-r108.json'
F=FIX/'fill-reports/fill-report-c1-r108.json'
SCRIPT=Path(__file__).resolve().parents[2]/'skills/lt-module-answer/scripts/reconcile.py'


def read(path):return json.loads(path.read_text())
def check(doc):return validate.validate_doc(doc,'reconciled',module=read(M),base_dir=R.parent)


def test_fixture_coverage_preserves_draft_and_feedback_only_note():
    before=A.read_bytes();report=read(F)
    assert set(reconcile.covered(report))=={'p1-11','p1-12','p1-13'}
    assert check(read(R)).exit_code==0
    work=reconcile.worklist(read(M),read(A),report)
    assert {i['item_id'] for i in work['items']}=={'p1-11','p1-12','p1-13'}
    assert A.read_bytes()==before
    first=read(R)['items'][0]
    assert first['outcome']=='combined' and first['note']==work['items'][0]['lt_feedback']
    for iid in ('p1-11','p1-12','p1-13'):
        bad=copy.deepcopy(report);next(i for i in bad['items'] if i['item_id']==iid)['verified']=False
        assert iid not in reconcile.covered(bad)


@pytest.mark.parametrize('mutation,rail',[
    (lambda d:d['items'].pop(),'reconciled-coverage'),
    (lambda d:d['items'].append(dict(d['items'][0],item_id='extra')),'reconciled-coverage'),
    (lambda d:d['items'][0]['lt_state'].update(proposed_selection=['o3']),'reconciled-state'),
    (lambda d:d['items'][0]['segments'][1].update(text='Changed'),'reconciled-draft-verbatim'),
    (lambda d:(d['items'][0].update(segments=d['items'][0]['segments'][:1],dropped=[])),'reconciled-draft-verbatim'),
    (lambda d:d['items'][0]['segments'][2].update(provenance_refs=[]),'reconciled-provenance'),
    (lambda d:d['items'][0]['segments'][2].update(provenance_refs=['missing']),'reconciled-provenance'),
    (lambda d:d['items'][0]['segments'][0].update(provenance_refs=['p1']),'reconciled-provenance'),
    (lambda d:d['items'][1]['dispute'].update(evidence=[]),'reconciled-dispute'),
    (lambda d:d['items'][1]['dispute']['evidence'][0].update(ref='feedback'),'reconciled-dispute'),
    (lambda d:d['items'][0].update(dispute=d['items'][1]['dispute']),'reconciled-dispute'),
    (lambda d:d['source_answers'].update(sha256='0'*64),'reconciled-binding'),
    (lambda d:d['source_fill_report'].update(sha256='0'*64),'reconciled-binding'),
])
def test_each_reconcile_rail(mutation,rail):
    doc=read(R);mutation(doc);result=check(doc)
    assert result.exit_code==1,(result.schema_errors,result.rail_errors)
    assert any(rail in e for e in result.rail_errors),result.rail_errors


def test_denied_keys_and_long_quote_and_schema_inference():
    d=read(R);d['items'][0]['segments'][0]['correctAnswer']='bad'
    assert any('additional' in e.lower() for e in check(d).schema_errors)
    assert validate.scan_denied_keys(d)
    d=read(R);d['items'][1]['dispute']['evidence'][0]['quote']='X'*301
    assert check(d).exit_code==2
    assert validate.infer_schema('reconciled.json')=='reconciled.schema.json'
    assert validate.infer_schema('reconcile-worklist.json') is None


def test_prepare_check_exits_and_answers_bytes(run_py,tmp_path):
    before=A.read_bytes()
    r=run_py(str(SCRIPT),'prepare','--module',str(M),'--answers',str(A),'--fill-report',str(F),'--out',str(tmp_path/'work.json'))
    assert r.returncode==0,r.stderr
    r=run_py(str(SCRIPT),'check','--module',str(M),'--answers',str(A),'--fill-report',str(F),'--reconciled',str(R))
    assert r.returncode==0,r.stderr
    d=read(R);d['items']='bad';p=tmp_path/'reconciled.json';p.write_text(json.dumps(d))
    r=run_py(str(SCRIPT),'check','--module',str(M),'--answers',str(A),'--fill-report',str(F),'--reconciled',str(p))
    assert r.returncode==2,r.stderr
    assert A.read_bytes()==before


def test_feedback_alone_cannot_support_dispute():
    d=read(R);d['items'][0].update(outcome='disputed',dispute={'lt_part':'state','evidence':[{'ref':'feedback','quote':d['items'][0]['note'],'locator':'feedback','contradiction':'opposite'}]})
    assert any('reconciled-dispute' in e for e in check(d).rail_errors)


def test_rebind_preserves_segments_and_rejects_non_rat_changes():
    previous=read(F);final=copy.deepcopy(previous);d=read(R)
    # Only report bookkeeping changes without a sheet fill.
    final['ended_at']='2000-01-01T00:00:01Z'
    result=reconcile.rebind_document(d,previous,final)
    assert result==d
    final['items'][0]['check_answer']['lt_feedback']='changed'
    with pytest.raises(ValueError,match='coverage|non-RAT'):
        reconcile.rebind_document(d,previous,final)
    final=copy.deepcopy(previous);final['check_answer']['presses']+=1
    with pytest.raises(ValueError,match='summary'):
        reconcile.rebind_document(d,previous,final)


def test_research_tier_two_and_rat_root_quarantine(tmp_path):
    d=read(R);d['items'][0]['provenance'][0]['tier']=2
    result=check(d)
    assert any('reconciled-provenance' in e for e in result.rail_errors)
    root=tmp_path/'rat';root.mkdir();cfg=tmp_path/'lt-suite.toml';cfg.write_text('[rat]\nanswers_dir='+json.dumps(str(root))+'\n')
    d=read(R);d['items'][0]['provenance'][0]['locator']=str(root/'sheet.md')
    result=validate.validate_doc(d,'reconciled',module=read(M),base_dir=R.parent,config_path=cfg)
    assert any('reconciled-provenance' in e for e in result.rail_errors)
    assert not list(root.iterdir())


@pytest.mark.parametrize('artifact', ['check-answer.json','reconcile-worklist.json','reconciled.json'])
@pytest.mark.parametrize('field', ['source_id','locator'])
def test_research_rejects_check_and_reconciliation_artifacts(artifact,field):
    # Lt feedback and combined outputs cannot supply a research tier through a file citation.
    doc=read(R)
    source=doc['items'][0]['provenance'][0]
    source[field]=artifact if field=='source_id' else artifact+'#checks[0].items[0].feedback'
    result=check(doc)
    assert result.exit_code==1,(result.schema_errors,result.rail_errors)
    assert any('reconciled-provenance' in error for error in result.rail_errors)


def test_rebind_cli_is_atomic_and_never_reads_sheets(run_py,tmp_path):
    # Keep all source pins absolute in the scratch copy.
    d=read(R);d['source_answers']['path']=str(A);d['source_fill_report']['path']=str(F)
    rp=tmp_path/'reconciled.json';rp.write_text(json.dumps(d));before=rp.read_bytes()
    final=read(F)
    final['source_answers']['path']=str(A)
    final['source_page_kinds']['path']=str(FIX/'page-kinds/page-kinds-c1-r108.json')
    final['source_check_answer']['path']=str(FIX/'runs/c1-r108/check-answer.json')
    # A changed ordinary entry must refuse without touching R.
    final['items'][0]['stopped_reason']='changed'
    fp=tmp_path/'fill-report.json';fp.write_text(json.dumps(final))
    r=run_py(str(SCRIPT),'rebind','--module',str(M),'--answers',str(A),'--fill-report',str(fp),'--previous-fill-report',str(F),'--reconciled',str(rp))
    assert r.returncode==3,r.stdout+r.stderr
    assert rp.read_bytes()==before


def test_digest_pin_repair_leaves_reconciled_bytes(run_py,tmp_path):
    p=tmp_path/'reconciled.json';p.write_bytes(R.read_bytes());before=p.read_bytes()
    script=Path(__file__).resolve().parents[2]/'shared/ltcore/digest.py'
    r=run_py(str(script),'--fix-pins',str(tmp_path))
    assert r.returncode==0,r.stderr
    assert p.read_bytes()==before


def test_T87_rebind_after_synthetic_rat_fill_and_export(run_py,tmp_path,monkeypatch):
    from ltcore import rat,page_kinds,check_answer as ca,config
    import importlib.util
    # c1-r109 carries its sheet exercise. Prepend the ordinary check page from c1-r108.
    module=read(FIX/'modules/module-c1-r109.json');ordinary=copy.deepcopy(read(M)['pages'][0])
    for page in module['pages']:
        old=page['page_index'];page['page_index']+=1
        if page['page_no'] is not None:page['page_no']+=1
        if page['url']:page['url']=page['url'].rsplit('/',1)[0]+'/'+str(old+1)
        for item in page['items']:item['item_id']='p'+str(old+1)+'-'+item['panel_key']
    ordinary['url']=ordinary['url'].replace('runs/108','runs/109')
    module['pages'].insert(0,ordinary)
    from collections import Counter
    all_items=[i for p in module['pages'] for i in p['items']]
    module['census'].update(pages_total=8,captured_total=len(all_items),answerable_total=len(all_items),by_type=dict(Counter(i['type'] for i in all_items)))
    module['lesson']['page_count']=8;module['commit_boundaries']['single_attempt_items']=['p1-18']
    apath=tmp_path/'answers.json';mpath=tmp_path/'module.json';mpath.write_text(json.dumps(module))
    ans=read(A);ans.update(module_id='c1-r109',run_id=109,source_module={'path':str(mpath),'sha256':reconcile.sha(mpath)})
    ans['items']=[a for a in ans['items'] if a['item_id'].startswith('p1-')]
    for p in module['pages'][1:]:
        for it in p['items']:ans['items'].append({'item_id':it['item_id'],'status':'skipped','skip_reason':'rat-sheet' if p['page_index']==4 else 'user-excluded'})
    apath.write_text(json.dumps(ans));root=tmp_path/'sheets';root.mkdir()
    synthetic=(FIX/'curriculum/Block A/RATs/1.2-team.md').read_text().replace('page: 3','page: 4')
    (root/'team.md').write_text(synthetic)
    cp=tmp_path/'lt-suite.toml';cp.write_text('[rat]\ntrat="from_dir"\nanswers_dir='+json.dumps(str(root))+'\n[paths]\ndata_dir='+json.dumps(str(tmp_path/'data'))+'\n[export.outputs]\npdf=false\n')
    cfg=config.load(cp);policy=rat.policy_from_config(cfg)
    rec=page_kinds.classify(module,policy,answers=ans,answers_sha256=reconcile.sha(apath))
    pk=tmp_path/'page-kinds.json';page_kinds.atomic_write(pk,rec)
    decisions=[rat.decide(p,policy,rec,ans) for p in module['pages'] if page_kinds.is_candidate(p,policy)]
    team=next(p for p in decisions if p['page_index']==4)
    assert team['decision']=='fill-from-sheet'
    ledger=read(FIX/'runs/c1-r108/check-answer.json');ledger['checks']=ledger['checks'][:1]
    lp=tmp_path/'check-answer.json';lp.write_text(json.dumps(ledger))
    previous=read(F);previous.update(module_id='c1-r109',run_id=109,source_answers={'path':str(apath),'sha256':reconcile.sha(apath)},
        source_page_kinds={'path':str(pk),'sha256':reconcile.sha(pk)},source_check_answer={'path':str(lp),'sha256':reconcile.sha(lp)},pages_filled=[1],rat_pages=decisions)
    previous['rat_pages']=[{k:v for k,v in d.items() if k not in {'question','reasons','check_reason'}} for d in decisions]
    previous['items']=[i for i in previous['items'] if i['item_id'].startswith('p1-')]
    for p in module['pages'][1:]:
        for it in p['items']:previous['items'].append({'item_id':it['item_id'],'item_type':it['type'],'attempts':it['attempts'],'attempted':False,'method':'not-attempted','result':'skipped','verified':False,'stopped_reason':'before RAT entry'})
    previous['check_answer']=ca.summary(previous['items']);previous.pop('save_waits',None)
    pp=tmp_path/'fill-report-pre-rat.json';pp.write_text(json.dumps(previous))
    rdoc=read(R);rdoc.update(module_id='c1-r109',source_answers=previous['source_answers'],source_fill_report={'path':str(pp),'sha256':reconcile.sha(pp)})
    rp=tmp_path/'reconciled.json';rp.write_text(json.dumps(rdoc));answer_bytes=apath.read_bytes();segments=[i['segments'] for i in rdoc['items']]
    final=copy.deepcopy(previous);final['pages_filled']=[1,4]
    for n,it in enumerate(module['pages'][3]['items'],1):
        row=next(i for i in final['items'] if i['item_id']==it['item_id'])
        row.update(attempted=True,method='view-model',result='filled',verified=True,
            receipt={'expected_hash':'101','observed_hash':'101','observed_at':'2000-01-01T00:00:00Z'},
            answer_source={**{k:team['sheet'][k] for k in ('root','name','sha256')},'kind':'rat-sheet','question':n,'page_kind':'trat','classified_by':team['sources']})
    fp=tmp_path/'fill-report.json';fp.write_text(json.dumps(final))
    spec=importlib.util.spec_from_file_location('reconcile_entry',SCRIPT);entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
    reads=[]
    monkeypatch.setattr(rat,'read_sheet',lambda *a,**kw:reads.append(a) or (_ for _ in ()).throw(AssertionError('sheet read during rebind')))
    rc=entry.main(['rebind','--module',str(mpath),'--answers',str(apath),'--fill-report',str(fp),'--previous-fill-report',str(pp),'--reconciled',str(rp),'--config',str(cp)])
    assert rc==0 and reads==[]
    assert apath.read_bytes()==answer_bytes
    updated=read(rp);assert [i['segments'] for i in updated['items']]==segments
    assert updated['source_fill_report']['sha256']==reconcile.sha(fp)
    exporter=Path(__file__).resolve().parents[2]/'skills/lt-module-export/scripts/export_module.py'
    r=run_py(str(exporter),'--module',str(mpath),'--answers',str(apath),'--fill-report',str(fp),'--reconciled',str(rp),'--config',str(cp),'--out-dir',str(tmp_path/'export'),'--week','1.2')
    assert r.returncode==0,r.stdout+r.stderr


def test_reconcile_usage_errors_are_three(run_py):
    r=run_py(str(SCRIPT),'prepare')
    assert r.returncode==3
