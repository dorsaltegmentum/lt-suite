# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""RAT plan/payload/report contracts T24 and T29 to T32 on invented c1-r109."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from ltcore import config, page_kinds, rat, validate

FIX = Path(__file__).resolve().parents[1] / 'fixtures'


@pytest.fixture
def rat_run(kit):
    mod = json.loads((FIX / 'modules/module-c1-r109.json').read_text(encoding="utf-8"))
    ans = json.loads((FIX / 'answers/answers-c1-r109.json').read_text(encoding="utf-8"))
    root = kit.tmp / 'sheets'
    root.mkdir()
    for p in (FIX / 'curriculum/Block A/RATs').iterdir():
        if p.is_file() and not p.name.startswith('.'):
            (root / p.name).write_bytes(p.read_bytes())
    kit.config(f'[paths]\ndata_dir = {json.dumps(str(kit.data))}\n[rat]\nanswers_dir = {json.dumps(str(root))}\ntrat = "from_dir"\n')
    kit.write('module.json', mod)
    kit.write('answers.json', ans)
    cfg = config.load(kit.tmp / 'lt-suite.toml')
    path = kit.run_dir('c1-r109') / 'page-kinds.json'
    record = page_kinds.classify(mod, rat.policy_from_config(cfg), answers=ans, path=path, answers_sha256=hashlib.sha256((kit.tmp / "answers.json").read_bytes()).hexdigest())
    return kit, mod, ans, cfg, record, path


def test_T29_plan_and_mark(rat_run, emit_mod):
    kit, mod, ans, cfg, record, path = rat_run
    import plan_fill
    plan = plan_fill.plan(mod, ans, False, cfg, answers_sha256=hashlib.sha256((kit.tmp / "answers.json").read_bytes()).hexdigest())
    assert {s['page_index']: s['reason'] for s in plan['skipped_pages']} == {
        2: 'irat-policy', 5: 'rat-unclassified', 6: 'rat-unclassified', 7: 'report-page'}
    pg = next(p for p in plan['pages'] if p['page_index'] == 3)
    assert all(i['fillable'] and i['answer_source'] == 'rat-sheet' for i in pg['items'])
    record = page_kinds.mark(mod, record, 5, 'not-rat', 'Practice quiz')
    page_kinds.atomic_write(path, record)
    assert 5 in [p['page_index'] for p in plan_fill.plan(mod, ans, False, cfg, answers_sha256=hashlib.sha256((kit.tmp / "answers.json").read_bytes()).hexdigest())['pages']]


def test_T30_payload_jobs_todos_exclusion_and_staleness(rat_run, emit_mod):
    kit, mod, ans, cfg, record, path = rat_run
    rc, pay, out = kit.payload(mod, ans)
    assert rc == 0, out
    pg = pay['pages']['3']
    assert [j['want'] for j in pg['jobs']] == [[1], [3], [0, 2], [0]]
    assert all(j['source'] == 'rat-sheet' and j['sheet']['name'] == '1.2-team.md' for j in pg['jobs'])
    assert pg['jobs'][1]['want_text'] == ['D. Desert oasis']
    assert [t['item'] for t in pay['todo']] == ['p3-84']
    groups, _ = emit_mod.group_pages(pay, 5, 5, {'ok': True}, {'ok': True}, False)
    assert groups[-1] == ['3']
    rc, pay, out = kit.payload(mod, ans, '--exclude', 'p3-81')
    assert rc == 0 and 'p3-81' not in [j['item'] for j in pay['pages']['3']['jobs']], out
    (kit.tmp / 'sheets/1.2-team.md').write_text('changed', encoding="utf-8", newline="\n")
    assert kit.payload(mod, ans)[0] == 3


def test_T30_missing_answer_refuses_and_single_attempt_held(rat_run):
    kit, mod, ans, cfg, record, path = rat_run
    ans['items'] = [a for a in ans['items'] if a['item_id'] != 'p3-81']
    kit.write("answers.json", ans)
    page_kinds.classify(mod, rat.policy_from_config(cfg), answers=ans, path=path, answers_sha256=hashlib.sha256((kit.tmp / "answers.json").read_bytes()).hexdigest())
    assert kit.payload(mod, ans)[0] == 3
    ans = json.loads((FIX / 'answers/answers-c1-r109.json').read_text(encoding="utf-8"))
    cap = mod['pages'][2]['items'][0]
    cap['attempts'] = 'single'
    ans['source_module']['question_digest'] = __import__('ltcore.digest', fromlist=['question_digest']).question_digest(mod)
    kit.write("answers.json", ans)
    page_kinds.classify(mod, rat.policy_from_config(cfg), answers=ans, path=path, answers_sha256=hashlib.sha256((kit.tmp / "answers.json").read_bytes()).hexdigest())
    rc, pay, out = kit.payload(mod, ans)
    assert rc == 0, out
    assert {'item': 'p3-81', 'page': 3, 'reason': 'refused-single-attempt'} in pay['refused']
    assert 'p3-81' not in [j['item'] for j in pay['pages']['3']['jobs']]


def test_T30_scripts_rat_names_and_last_group(rat_run):
    kit, mod, ans, cfg, record, path = rat_run
    rc, pay, out = kit.payload(mod, ans)
    assert rc == 0, out
    r = kit.emit('scripts', '--payload', kit.tmp / 'out/fill-payload.json', '--out', kit.tmp / 'out', '--no-render')
    assert r.returncode == 0, r.stdout+r.stderr
    manifest = kit.read('out/scripts.json')
    calls = [c for c in manifest['calls'] if c['kind'] == 'fill']
    assert calls[-1]['name'] == '_fill-c1-r109-rat-p3.js'
    assert calls[-1]['pages'] == [3]
    assert any(c['name'] == '_verify-c1-r109-rat-p3.js' for c in manifest['calls'])


def test_T31_drafts_need_confirmation_irat_never_drafted(rat_run):
    kit, mod, ans, cfg, record, path = rat_run
    ans['items'][5].update(status='drafted', segments=[], provenance=[])
    rec = page_kinds.classify(mod, rat.policy_from_config(cfg), answers=ans, path=path, answers_sha256=hashlib.sha256((kit.tmp / "answers.json").read_bytes()).hexdigest())
    d = rat.decide(mod['pages'][2], rat.policy_from_config(cfg), rec, ans)
    assert d['decision'] == 'rat-sheet-confirm' and 'drafts-exist' in d['reasons']
    ans['items'][0].update(status='drafted', segments=[], provenance=[])
    ctx = validate._Ctx(base_dir=kit.tmp, module=mod, page_kinds=rec)
    assert any('rat-irat-drafted' in s for s in validate.rails_rat_drafts(ans, ctx))


def test_T32_report_safe_sources_and_todos(rat_run, emit_mod):
    kit, mod, ans, cfg, record, path = rat_run
    sheet_receipts(kit, mod, ans, emit_mod, bound=True)
    r = kit.run('make_fill_report.py', '--module', 'module.json', '--answers', 'answers.json', '--receipts', 'receipts.json', '--notes', 'notes.json')
    assert r.returncode == 0, r.stdout+r.stderr
    rep = json.loads((kit.run_dir('c1-r109') / 'fill-report.json').read_text(encoding="utf-8"))
    items = {i['item_id']: i for i in rep['items']}
    assert items['p3-84']['result'] == 'todo'
    assert items['p3-81']['answer_source']['kind'] == 'rat-sheet'
    assert items['p2-81']['result'] == 'skipped' and 'irat-policy' in items['p2-81']['stopped_reason']
    assert len(rep['rat_pages']) == 4
    text = json.dumps(rep)
    for forbidden in ('Desert oasis', 'Amber spindle', '1. B'):
        assert forbidden not in text


def test_T24_legacy_explicit_draft_route(kit, emit_mod):
    import plan_fill
    mod = json.loads((FIX / 'modules/module-c1-r104.json').read_text(encoding="utf-8"))
    ans = json.loads((FIX / 'answers/answers-c1-r104.json').read_text(encoding="utf-8"))
    cfg = config.build({}, config.Location(None, 'none', kit.tmp/'config.toml', 'flag'))
    assert any(s['reason'] == 'rat-record-stale' for s in plan_fill.plan(mod, ans, True, cfg)['skipped_pages'])
    pol = rat.policy_from_config(cfg)
    rec = page_kinds.classify(mod, pol, answers=ans)
    assert rat.decide(mod['pages'][8], pol, rec, ans)['decision'] == 'rat-unclassified'
    rec = page_kinds.mark(mod, rec, 9, 'trat', 'Team test')
    rec = page_kinds.choose(mod, rec, pol, 9, 'draft', 'Draft the team test', answers=ans)
    plan = plan_fill.plan(mod, ans, False, cfg, record=rec)
    assert any(p['page_index'] == 9 for p in plan['pages'])


def test_T13_confirmed_partial_emits_only_positional_questions(rat_run):
    kit, mod, ans, cfg, record, path = rat_run
    root = kit.tmp / 'sheets'
    compact = (root / '1.2-team.md').read_text(encoding="utf-8")
    (root / '1.2-team.md').write_text(compact.split('4. ?')[0], encoding="utf-8", newline="\n")
    (root / '1.2-readiness.md').write_text('Synthetic notes without choices', encoding="utf-8", newline="\n")
    pol = rat.policy_from_config(cfg)
    record = page_kinds.classify(mod, pol, answers=ans,
                                answers_sha256=hashlib.sha256((kit.tmp/'answers.json').read_bytes()).hexdigest())
    d = rat.decide(mod['pages'][2], pol, record, ans)
    assert d['decision'] == 'rat-sheet-confirm' and 'partial' in d['reasons']
    record = page_kinds.choose(mod, record, pol, 3, 'sheet', 'Use questions 1 to 3', 'rat.answers_dir:1.2-team.md', answers=ans)
    page_kinds.atomic_write(path, record)
    rc, pay, out = kit.payload(mod, ans)
    assert rc == 0, out
    assert [j['item'] for j in pay['pages']['3']['jobs']] == ['p3-81', 'p3-82', 'p3-83']
    assert [t['item'] for t in pay['todo']] == ['p3-84', 'p3-85']
    assert d['sheet']['todo_questions'] == {'4': 'unaligned', '5': 'unaligned'}


def test_T24_unavailable_skip_evidence_uses_the_actual_legacy_fixture():
    mod = json.loads((FIX / 'modules/module-demo.json').read_text(encoding="utf-8"))
    ans = json.loads((FIX / 'answers/answers-demo.json').read_text(encoding="utf-8"))
    record = page_kinds.classify(mod, answers=ans)
    status = next(s for s in page_kinds.page_status(mod, record) if s['page_index'] == 2)
    assert status['kind'] == 'trat' and status['sources'] == ['answers-json']


@pytest.mark.parametrize('stale', [False, True])
def test_custom_marker_without_current_record_holds_plan(kit, emit_mod, fk, stale):
    import plan_fill
    mod = fk.module(fk.text_pages(1, 2))
    mod['pages'][0]['content'] = [{'text': 'Team examination'}]
    ans = fk.answers([fk.drafted(i['item_id']) for i in mod['pages'][0]['items']])
    cfg = config.build({'rat': {'kind_markers': {'trat': 'Team examination'}}},
                       config.Location(None, 'none', kit.tmp/'config.toml', 'flag'))
    pol = rat.policy_from_config(cfg)
    rec = page_kinds.classify(mod, pol, answers=ans) if stale else None
    if stale:
        mod['pages'][0]['items'][0]['prompt_text'] += ' changed'
        rec['_stale'] = True
    plan = plan_fill.plan(mod, ans, False, cfg, record=rec)
    assert plan['rat_pages'][0]['candidate'] is True
    assert plan['rat_pages'][0]['decision'] == 'rat-record-stale'
    assert not plan['pages']


def sheet_receipts(kit, mod, ans, emit_mod, bound=False):
    rc, pay, out = kit.payload(mod, ans)
    assert rc == 0, out
    payload = kit.tmp / 'out/fill-payload.json'
    rows = []
    for job in pay['pages']['3']['jobs']:
        value = emit_mod.expected_hash(job)
        row = dict(item_id=job['item'], expected_hash=value, observed_hash=value,
                   observed_at='2000-01-01T00:00:00Z')
        if bound:
            row['source_payload'] = {'path': str(payload), 'sha256': hashlib.sha256(payload.read_bytes()).hexdigest()}
        rows.append(row)
    kit.write('receipts.json', {'receipts': rows})
    kit.write('notes.json', {'approval_mode': 'manual', 'started_at': '2000-01-01T00:00:00Z',
                             'ended_at': '2000-01-01T00:00:00Z'})
    return rows


def report(kit):
    return kit.run('make_fill_report.py', '--module', 'module.json', '--answers', 'answers.json',
                   '--receipts', 'receipts.json', '--notes', 'notes.json')


@pytest.mark.parametrize('mutation', ['edited', 'missing'])
def test_sheet_receipts_cannot_rebind_to_current_classification(rat_run, emit_mod, mutation):
    kit, mod, ans, cfg, record, path = rat_run
    sheet_receipts(kit, mod, ans, emit_mod)
    if mutation == 'edited':
        sheet = kit.tmp / 'sheets/1.2-team.md'
        sheet.write_text(sheet.read_text(encoding="utf-8").replace('1. B', '1. A'), encoding="utf-8", newline="\n")
        page_kinds.classify(mod, rat.policy_from_config(cfg), answers=ans, path=path,
                           answers_sha256=hashlib.sha256((kit.tmp/'answers.json').read_bytes()).hexdigest())
    else:
        path.unlink()
    result = report(kit)
    assert result.returncode == 3, result.stdout + result.stderr
    assert 'RAT receipt binding' in result.stderr


@pytest.mark.parametrize("field", ["sha256", "root", "name"])
def test_report_sheet_identity_checked_against_pinned_record(rat_run, emit_mod, field):
    kit, mod, ans, cfg, record, path = rat_run
    sheet_receipts(kit, mod, ans, emit_mod, bound=True)
    result = report(kit)
    assert result.returncode == 0, result.stdout + result.stderr
    rep = json.loads((kit.run_dir('c1-r109')/'fill-report.json').read_text(encoding="utf-8"))
    for row in rep['rat_pages']:
        if row.get('sheet'):
            row['sheet'][field] = '0' * 64 if field == 'sha256' else 'rat.answers_dirs[1]' if field == 'root' else 'invented-other'
    for item in rep['items']:
        if item.get('answer_source'):
            item['answer_source'][field] = '0' * 64 if field == 'sha256' else 'rat.answers_dirs[1]' if field == 'root' else 'invented-other'
    result = validate.validate_doc(rep, 'fill-report', module=mod, config_path=kit.tmp/'lt-suite.toml')
    assert result.exit_code == 1, result
    assert any('rat-sheet-hash' in e for e in result.rail_errors)


@pytest.mark.parametrize('mutation', ['edited', 'missing', 'payload', 'expected'])
def test_bound_sheet_receipts_refuse_changed_execution_inputs(rat_run, emit_mod, mutation):
    kit, mod, ans, cfg, record, path = rat_run
    rows = sheet_receipts(kit, mod, ans, emit_mod, bound=True)
    if mutation == 'edited':
        sheet = kit.tmp / 'sheets/1.2-team.md'
        sheet.write_text(sheet.read_text(encoding="utf-8").replace('1. B', '1. A'), encoding="utf-8", newline="\n")
        page_kinds.classify(mod, rat.policy_from_config(cfg), answers=ans, path=path,
                           answers_sha256=hashlib.sha256((kit.tmp/'answers.json').read_bytes()).hexdigest())
    elif mutation == 'missing':
        path.unlink()
    elif mutation == 'payload':
        payload = kit.tmp/'out/fill-payload.json'
        payload.write_bytes(payload.read_bytes() + b' ')
    else:
        rows[0]['expected_hash'] = rows[0]['observed_hash'] = 12345
        kit.write('receipts.json', {'receipts': rows})
    result = report(kit)
    assert result.returncode == 3, result.stdout + result.stderr
    assert 'RAT receipt binding' in result.stderr
    assert not (kit.run_dir('c1-r109')/'fill-report.json').exists()


@pytest.mark.parametrize('binding', ['correct', 'missing', 'wrong'])
def test_record_sheet_receipts_bind_browser_payload(rat_run, emit_mod, binding):
    kit, mod, ans, cfg, record, path = rat_run
    rows = sheet_receipts(kit, mod, ans, emit_mod, bound=True)
    # record must create its own receipts from the browser results.
    (kit.tmp/'receipts.json').unlink()
    payload_hash = rows[0]['source_payload']['sha256']
    fill = {'ok': True, 'module': mod['module_id'], 'page': 3,
            'items': [{'item': r['item_id'], 'result': 'written'} for r in rows]}
    ver = {'ok': True, 'fresh_load': True, 'module': mod['module_id'],
           'observed_at': '2000-01-01T00:00:00Z',
           'items': [[r['item_id'], 'ok', 1, r['observed_hash']] for r in rows]}
    if binding != 'missing':
        fill['source_payload_sha256'] = ver['source_payload_sha256'] = payload_hash if binding == 'correct' else '0'*64
    kit.write('fill.json', fill)
    kit.write('verify.json', ver)
    result = kit.emit('record', '--module', 'module.json', '--fill', 'fill.json',
                      '--verify', 'verify.json', '--payload', 'out/fill-payload.json')
    if binding == 'correct':
        assert result.returncode == 0, result.stdout + result.stderr
        receipts = json.loads((kit.run_dir(mod['module_id'])/'receipts.json').read_text(encoding="utf-8"))['receipts']
        assert [r['source_payload'] for r in receipts] == [r['source_payload'] for r in rows]
        kit.write('receipts.json', {'receipts': receipts})
        assert report(kit).returncode == 0
    else:
        assert result.returncode == 3, result.stdout + result.stderr
        assert 'RAT receipt binding' in result.stderr


def test_stale_answers_classification_names_refresh_before_receipt_binding(rat_run, emit_mod):
    kit, mod, ans, cfg, record, path = rat_run
    sheet_receipts(kit, mod, ans, emit_mod)
    ans['drafted_at'] = '2000-01-02T00:00:00Z'
    kit.write('answers.json', ans)
    result = report(kit)
    assert result.returncode == 3
    assert 'page-kinds.json is stale for this answers.json; run page_kinds.py classify' in result.stderr


@pytest.mark.parametrize('redacted', [False, True])
def test_report_binds_redacted_payload_hash_by_unique_short_prefix(rat_run, emit_mod, redacted):
    kit, mod, ans, cfg, record, path = rat_run
    rows = sheet_receipts(kit, mod, ans, emit_mod, bound=True)
    for row in rows:
        pin = row['source_payload']
        pin['sha16'] = pin['sha256'][:16]
        if redacted:
            pin['sha256'] = '[BLOCKED: Base64 encoded data]'
        else:
            del pin['sha256']
    kit.write('receipts.json', {'receipts': rows})
    result = report(kit)
    assert result.returncode == 0, result.stdout + result.stderr
    result_doc = json.loads((kit.run_dir('c1-r109') / 'fill-report.json').read_text(encoding='utf-8'))
    assert 'source_payload_sha16' in next(r for r in result_doc['items'] if r['item_id'] == 'p3-81')['stopped_reason']
