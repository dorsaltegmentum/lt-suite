# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check generation consumes J3's one store and refuses unsafe drafts (53,68-74)."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from ltcore import check_answer as ca, page_kinds, rat, config
from fillkit import item, module, drafted, answers


def setup_check(kit, *, pages=1, classify=False):
    kit.config('[fill.check_answer]\nenabled=true\n')
    cap=lambda p,k:item(f'p{p}-{k}',str(k),'multiple-choice','select',assessment_type='instantFeedback',
        options=[{'option_id':'o1','text':'A'},{'option_id':'o2','text':'B'}],constraints={'note':'single-select'})
    mod=module([(p,[cap(p,p*10+k) for k in range(1,4)] if pages>1 else [cap(p,11)]) for p in range(1,pages+1)])
    ans=answers([drafted(c['item_id'],proposed_selection=['o1']) for p in mod['pages'] for c in p['items']])
    mpath=kit.write('module.json',mod);ans['source_module']={'path':str(mpath),'sha256':hashlib.sha256(mpath.read_bytes()).hexdigest()}
    apath=kit.write('answers.json',ans)
    if classify:
        rec=page_kinds.classify(mod,rat.policy_from_config(config.load(kit.tmp/'lt-suite.toml')),answers=ans,answers_sha256=hashlib.sha256(apath.read_bytes()).hexdigest())
        rec=page_kinds.mark(mod,rec,1,'trat','Team test')
        rec=page_kinds.mark(mod,rec,2,'not-rat','Not a test')
        page_kinds.atomic_write(kit.run_dir()/'page-kinds.json',rec)
    return mod,ans


def owned_payload(kit,mod,ans):
    # Existing generator remains the source of job hashes.
    result=kit.emit('payload','--module','module.json','--answers','answers.json')
    assert result.returncode in (0,2),result.stderr
    pay=kit.read('data/runs/c1-r101/fill-js/fill-payload.json')
    jobs={j['item']:j for p in pay['pages'].values() for j in p['jobs']}
    written=[{'item':iid,'result':'written'} for iid in jobs]
    kit.write('fill.json',{'ok':True,'module':'c1-r101','items':written})
    receipts=[{'item_id':iid,'expected_hash':ca.hashes('select','0',[]).split('/')[0],'observed_hash':ca.hashes('select','0',[]).split('/')[0],'observed_at':'2000-01-01T00:00:00Z'} for iid in jobs]
    # Normal fill receipts carry numeric text hashes.
    for r in receipts:r['expected_hash']=r['observed_hash']=int(r['expected_hash'])
    kit.write('data/runs/c1-r101/receipts.json',{'receipts':receipts})
    kit.write('data/state/calibration.json',{'https://lt.example.invalid':{'save':{'version':1,'method':'PUT','path_template':'/api/panels/{uuid}/state','source':'net','client_sig':'synthetic'}}})
    return jobs


def check(kit,*args):
    return kit.emit('check','--module','module.json','--fill','fill.json','--no-render',*args)


def test_disabled_and_missing_stale_classification_exit_codes(kit):
    mod,ans=setup_check(kit,pages=3)
    kit.config('')
    r=kit.emit('check','--module','module.json','--no-render')
    assert r.returncode==4
    kit.config('[fill.check_answer]\nenabled=true\n')
    # Build a minimal valid preexisting payload without reading a sheet.
    pay={'schema':'lt-fill-payload/2','module_id':mod['module_id'],'module':{'question_digest':__import__('ltcore.digest',fromlist=['question_digest']).question_digest(mod)},
         'answers':{'path':str(kit.tmp/'answers.json'),'sha256':hashlib.sha256((kit.tmp/'answers.json').read_bytes()).hexdigest()},'pages':{}}
    kit.write('data/runs/c1-r101/fill-js/fill-payload.json',pay)
    r=check(kit)
    assert r.returncode==3 and 'page_kinds.py classify --json' in r.stderr


def test_three_unflagged_quiz_pages_use_one_store_and_only_not_rat_emits(kit):
    mod,ans=setup_check(kit,pages=3,classify=True)
    assert not any(p.get('rat_candidate') for p in mod['pages'])
    owned_payload(kit,mod,ans)
    r=check(kit)
    assert r.returncode==0,r.stderr
    plan=kit.read('data/runs/c1-r101/fill-js/check-plan.json')
    assert plan['check_answer_keys']==[[2,str(k)] for k in (21,22,23)]
    reasons={r['item']:r['reason'] for r in plan['refused']}
    assert reasons['p1-11']=='rat-page' and reasons['p3-31']=='rat-unmarked'
    assert not (kit.run_dir()/'rat-pages.json').exists()


@pytest.mark.parametrize('result',['already-new','occupied-skipped','write-mismatch'])
def test_ownership_needs_actual_written_or_replaced_result(kit,result):
    mod,ans=setup_check(kit);owned_payload(kit,mod,ans)
    kit.write('fill.json',{'ok':True,'module':'c1-r101','items':[{'item':'p1-11','result':result}]})
    r=check(kit)
    assert r.returncode==2 and 'draft-not-written' in r.stdout


def test_calibration_and_reserve_are_required(kit):
    mod,ans=setup_check(kit);owned_payload(kit,mod,ans)
    kit.write('data/state/calibration.json',{})
    r=check(kit);assert r.returncode==2 and 'origin-uncalibrated' in r.stdout
    kit.config('[fill.check_answer]\nenabled=true\n[fill.dialog]\nmax_ms=80000\n')
    r=check(kit);assert r.returncode==3 and 'reserve' in r.stderr


@pytest.mark.parametrize('field,value,reason',[
    ('attempts','single','single-attempt'),('attempts',None,'single-attempt'),
    ('assessment_type','graded','assessment-graded'),('assessment_type','noAssessment','assessment-noAssessment'),
    ('type','text-response','type-not-checkable'),('type','table-completion','type-not-checkable'),
    ('parent_item_id','p1-parent','in-container'),('read_only',None,'read-only-or-unknown'),('read_only',True,'read-only-or-unknown')])
def test_static_refusal_table(field,value,reason):
    cap={'item_id':'p1-K','type':'multiple-choice','attempts':'multi','assessment_type':'instantFeedback','parent_item_id':None,'read_only':False,'commit_state':'open'}
    cap[field]=value
    assert ca.eligibility(cap,{'page_index':1},{'pages':[{'page_index':1}]},{'rat':False,'check_allowed':True},written=True,verified=True,calibrated=True)==reason


def test_markers_and_match_evidence_veto_informed_marks():
    for source in ('page-text','rat-answers'):
        cap={'item_id':'p1-K','type':'multiple-choice','attempts':'multi','assessment_type':'instantFeedback'}
        status={'rat':False,'check_allowed':False,'sources':[source,'user-mark'],'check_reason':'rat-page'}
        assert ca.eligibility(cap,{'page_index':1},{},status)=='rat-page'


def test_record_and_check_verify_preserve_policy_after_disable(kit):
    mod,ans=setup_check(kit);owned_payload(kit,mod,ans);assert check(kit).returncode==0
    h=ca.hashes('select','0',[])
    row={'item_id':'p1-11','kind':'select','draft_h':h,'draft_state':'0','final_h':h,'state':'0','stop':'correct','moved':False,'presses_before':0,
         'presses':['0|correct|-|-'],'feedback':'','pool_h':[],'tried':['0'],'wrote':False,'save_signal':None,'final_verdict':'correct'}
    plan=kit.read('data/runs/c1-r101/fill-js/check-plan.json')
    receipt={'ok':True,'module':'c1-r101','mode':'check','page':1,'policy':{'enabled':True,'config':'a'},'rat_page':plan['rat_pages'][0],'doc_id':'checked','items':[row]}
    kit.write('check.json',receipt)
    r=kit.emit('check-verify','--module','module.json','--check','check.json','--no-render')
    assert r.returncode==0,r.stderr
    assert h in (kit.run_dir()/'fill-js/_verify-check-c1-r101.js').read_text(encoding="utf-8")
    kit.config('')
    kit.write('verify.json',{'ok':True,'module':'c1-r101','mode':'check-verify','fresh_load':True,'doc_id':'fresh','items':[['p1-11','ok',0,h]],'observed_at':'2000-01-01T00:00:01Z'})
    r=kit.emit('record','--module','module.json','--check','check.json','--verify','verify.json')
    assert r.returncode==0,r.stderr
    assert kit.read('data/runs/c1-r101/check-answer.json')['checks'][0]['policy']['enabled'] is True
    assert kit.read('data/state/calibration.json')['https://lt.example.invalid']['save']


@pytest.mark.parametrize('wrote',[False,True])
def test_only_verify_miss_after_confirmed_check_write_drops_save(kit,wrote):
    mod,ans=setup_check(kit);owned_payload(kit,mod,ans);assert check(kit).returncode==0
    h=ca.hashes('select','1',[])
    row={'item_id':'p1-11','kind':'select','draft_h':ca.hashes('select','0',[]),'draft_state':'0','final_h':h,'state':'1','stop':'correct','moved':True,'presses_before':0,'presses':['0|incorrect|-|-','1|correct|-|-'],'feedback':'','pool_h':[],'tried':['0','1'],'wrote':wrote,'save_signal':'net' if wrote else None,'final_verdict':'correct'}
    kit.write('check.json',{'ok':True,'module':'c1-r101','mode':'check','page':1,'policy':{'enabled':True,'config':'x'},'rat_page':{},'items':[row]})
    kit.write('verify.json',{'ok':True,'module':'c1-r101','mode':'check-verify','fresh_load':True,'items':[['p1-11','BAD',0,'wrong']]})
    before=kit.read('data/state/calibration.json')
    r=kit.emit('record','--module','module.json','--check','check.json','--verify','verify.json')
    assert r.returncode==0,r.stderr
    after=kit.read('data/state/calibration.json')
    assert ('save' in after['https://lt.example.invalid']) is not wrote
    assert after['https://lt.example.invalid'].get('cross_page')==before['https://lt.example.invalid'].get('cross_page')


@pytest.mark.parametrize('status', ['BAD', 'no-panel', None])
@pytest.mark.parametrize('wrote', [False, True])
def test_record_check_requires_ok_status_even_with_matching_hashes(kit, emit_mod, status, wrote):
    # A shape failure can return the same selection hashes as a successful verify.
    import make_fill_report
    mod,ans=setup_check(kit);owned_payload(kit,mod,ans);assert check(kit).returncode==0
    h=ca.hashes('select','1',[])
    row={'item_id':'p1-11','kind':'select','draft_h':ca.hashes('select','0',[]),'draft_state':'0',
         'final_h':h,'state':'1','stop':'correct','moved':True,'presses_before':0,
         'presses':['0|incorrect|-|-','1|correct|-|-'],'feedback':'','pool_h':[],
         'tried':['0','1'],'wrote':wrote,'save_signal':'net' if wrote else None,'final_verdict':'correct'}
    kit.write('check.json',{'ok':True,'module':'c1-r101','mode':'check','page':1,
                          'policy':{'enabled':True,'config':'x'},'rat_page':{},'items':[row]})
    kit.write('verify.json',{'ok':True,'module':'c1-r101','mode':'check-verify','fresh_load':True,
                           'observed_at':'2000-01-01T00:00:01Z','items':[['p1-11',status,0,h]]})
    result=kit.emit('record','--module','module.json','--check','check.json','--verify','verify.json')
    assert result.returncode==0,result.stderr
    assert 'verify misses: 1' in result.stdout
    receipts=kit.read('data/runs/c1-r101/receipts.json')
    latest=receipts['receipts'][-1]
    assert latest['expected_hash']==h and latest['observed_hash']!=h
    notes={'approval_mode':'manual','started_at':'2000-01-01T00:00:00Z','ended_at':'2000-01-01T00:00:02Z'}
    report,_=make_fill_report.build(mod,ans,receipts,notes,kit.tmp/'answers.json',None,
        cfg=config.load(kit.tmp/'lt-suite.toml'),check_ledger=kit.read('data/runs/c1-r101/check-answer.json'))
    assert report['items'][0]['result']=='failed' and report['items'][0]['verified'] is False
    assert ('save' in kit.read('data/state/calibration.json')['https://lt.example.invalid']) is not wrote


@pytest.mark.parametrize('change', ['title','attempts','assessment_type','rat_candidate','content'])
def test_classification_edits_force_check_refresh(kit,change):
    mod,ans=setup_check(kit,pages=3,classify=True);owned_payload(kit,mod,ans)
    if change=='title':mod['pages'][1]['title']='Revised practice'
    elif change=='content':mod['pages'][1]['content']=[{'text':'Team readiness assurance test'}]
    elif change=='rat_candidate':mod['pages'][1]['rat_candidate']=True
    else:mod['pages'][1]['items'][0][change]='single' if change=='attempts' else 'graded'
    kit.write('module.json',mod)
    r=check(kit)
    assert r.returncode==3 and 'classify --json' in r.stderr


def test_default_plan_grants_no_check_keys_and_has_no_check_script(kit):
    mod,ans=setup_check(kit);kit.config('')
    r=kit.emit('payload','--module','module.json','--answers','answers.json')
    assert r.returncode==0,r.stderr
    plan=kit.read('data/runs/c1-r101/fill-js/plan.json')
    assert 'check_answer_keys' not in plan
    assert not list((kit.run_dir()/'fill-js').glob('_check-*'))


def test_handback_data_names_previously_written_team_drafts(kit):
    mod,ans=setup_check(kit,pages=3,classify=True);owned_payload(kit,mod,ans)
    f=kit.read('fill.json');f['items'].extend({'item':f'p1-{k}','result':'written'} for k in (11,12,13));kit.write('fill.json',f)
    r=check(kit);assert r.returncode==0,r.stderr
    plan=kit.read('data/runs/c1-r101/fill-js/check-plan.json')
    assert plan['prior_drafts_on_rat'][0]['items']==['p1-11','p1-12','p1-13']
    assert plan['prior_drafts_on_rat'][0]['kind']=='trat'


def test_sorted_capture_pool_uses_actual_written_pool_indices(emit_mod):
    cap={'item_id':'p1-K','type':'image-labeling','attempts':'multi','options':[{'option_id':'o1','text':'A'},{'option_id':'o2','text':'Z'}],
         'constraints':{'note':'locations=2'}}
    job={'item':'p1-K','kind':'labels','placements':[{'label':'A','loc':0},{'label':'Z','loc':1}]}
    owned={'pool_h':[ca.djb2('Z'),ca.djb2('A')],'index_state':'0:1,1:0'}
    result=emit_mod._check_job(job,cap,owned)
    assert result['draft_state']=='0:1,1:0'
    assert result['pool_h']==owned['pool_h']
    pool=ca.ordered_pool(cap,owned['pool_h'])
    assert ca.state_text(cap,'labels',result['draft_state'],pool=pool)['label_placements']==[
        {'label':'Z','target':'anchor 1','target_index':1},{'label':'A','target':'anchor 0','target_index':0}]


def test_resume_emits_only_not_finished_and_preserves_verify_definitions(kit):
    mod,ans=setup_check(kit,pages=3,classify=True);owned_payload(kit,mod,ans)
    assert check(kit).returncode==0
    plan=kit.read('data/runs/c1-r101/fill-js/check-plan.json');status=next(s for s in plan['rat_pages'] if s['page_index']==2)
    rows=[]
    for key,stop in ((21,'not-finished'),(22,'correct'),(23,'not-finished')):
        state='1' if key==21 else '0'
        rows.append({'item_id':f'p2-{key}','kind':'select','draft_h':ca.hashes('select','0',[]),'draft_state':'0',
          'final_h':ca.hashes('select',state,[]),'state':state,'stop':stop,'moved':key==21,'presses_before':0,
          'presses':['0|incorrect|-|-'] if key==21 else ['0|correct|-|-'] if key==22 else [],
          'tried':['0'] if key!=23 else [],'right':[],'wrong':[],'wrote':key==21,'save_signal':'net' if key==21 else None})
    kit.write('check.json',{'ok':True,'module':'c1-r101','mode':'check','page':2,'policy':plan['policy'],'rat_page':status,'items':rows})
    r=check(kit,'--resume','check.json');assert r.returncode==0,r.stderr
    plan=kit.read('data/runs/c1-r101/fill-js/check-plan.json')
    assert plan['check_answer_keys']==[[2,'21'],[2,'23']]
    stored=kit.read('data/runs/c1-r101/fill-js/check-jobs.json')['pages']['2']
    assert {j['item'] for j in stored}=={'p2-21','p2-22','p2-23'}
    resumed=next(j for j in stored if j['item']=='p2-21')
    assert resumed['resume']['state']=='1' and resumed['resume']['presses_before']==1


@pytest.mark.parametrize('consumer', ['plan','check'])
def test_check_consumers_use_configured_rat_markers(kit, emit_mod, consumer):
    # Replacing default markers must also replace them in check planning.
    import plan_fill
    mod,ans=setup_check(kit)
    mod['pages'][0]['content']=[{'text':'iRAT terminology appears in an ordinary exercise.'}]
    path=kit.write('module.json',mod)
    ans['source_module']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    kit.write('answers.json',ans)
    kit.config('[fill.check_answer]\nenabled=true\n[rat.kind_markers]\nirat="CUSTOM_INDIVIDUAL"\ntrat="CUSTOM_TEAM"\n')
    if consumer=='plan':
        result=plan_fill.plan(mod,ans,False,config.load(kit.tmp/'lt-suite.toml'))
        assert result['check_answer']['items']==[{'item_id':'p1-11','page':1}]
    else:
        owned_payload(kit,mod,ans)
        result=check(kit)
        assert result.returncode==0,result.stdout+result.stderr
        plan=kit.read('data/runs/c1-r101/fill-js/check-plan.json')
        assert plan['check_answer_keys']==[[1,'11']]
        assert plan['rat_pages'][0]['candidate'] is False
    assert not (kit.run_dir()/'page-kinds.json').exists()


def test_check_render_pins_live_classification_inputs(kit):
    mod, ans = setup_check(kit)
    mod['pages'][0]['content'] = [{'panel_key': 'T', 'plugin_key': 'kcTextArea',
                                 'kind': 'text', 'text': 'Ordinary   exercise'}]
    path = kit.write('module.json', mod)
    ans['source_module']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    kit.write('answers.json', ans)
    owned_payload(kit, mod, ans)
    result = check(kit)
    assert result.returncode == 0, result.stderr
    source = next((kit.run_dir() / 'fill-js').glob('_check-*-p1.js')).read_text(encoding="utf-8")
    cfg = json.JSONDecoder().raw_decode(source.rsplit('return await LtCheck.run(', 1)[1])[0]
    assert cfg['classification_pin']['title'] == mod['pages'][0]['title']
    assert cfg['classification_pin']['content'] == {
        'T': {'plugin_key': 'kcTextArea', 'text': 'Ordinary exercise'}}
    assert cfg['classification_pin']['items']['11'] == {
        'plugin_key': 'select', 'attempts': 'multi', 'assessment_type': 'instantFeedback'}
    assert 'proposed_selection' not in cfg['classification_pin']['items']['11']


def test_check_inline_metadata_reuses_runner_and_keeps_direct_output(kit):
    mod, ans = setup_check(kit)
    (kit.run_dir() / "fill-js").mkdir(parents=True, mode=0o700)
    owned_payload(kit, mod, ans)
    result = kit.emit('check', '--module', 'module.json', '--fill', 'fill.json', '--stub')
    assert result.returncode == 0, result.stdout + result.stderr
    metadata = __import__('json').loads(result.stdout.splitlines()[0])
    assert metadata['cache'] is True
    assert metadata['body_sha256'] == metadata['payload']['body_sha256']
    assert metadata['payload']['chunks']
    loader = Path(metadata['path']).read_text(encoding='utf-8')
    assert 'localStorage.getItem(KEY)' in loader
    direct = kit.emit('check', '--module', 'module.json', '--fill', 'fill.json', '--no-stub')
    assert direct.returncode == 0, direct.stdout + direct.stderr
    assert Path(direct.stdout.splitlines()[0]).is_file()
    assert 'LtCheck.run' in Path(direct.stdout.splitlines()[0]).read_text(encoding='utf-8')
