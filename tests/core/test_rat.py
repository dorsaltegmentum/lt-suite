# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synthetic RAT contracts T1 to T26; no course or student material."""
import importlib.util
from pathlib import Path


def test_T1_rat_reader_is_a_shared_boundary():
    assert importlib.util.find_spec("ltcore.rat") is not None, "shared RAT reader is missing"


def test_T35_shared_status_exists():
    assert importlib.util.find_spec("ltcore.page_kinds") is not None, "shared page store is missing"

import copy
import json
import os
import stat
import sys

import pytest

from ltcore import config, page_kinds as pk, rat


def module():
    pools = [
        ['Amber spindle', 'Cobalt rotor', 'Silver helix', 'Bronze vessel'],
        ['River delta', 'Mountain summit', 'Ocean trench', 'Desert oasis'],
        ['Glass prism', 'Velvet curtain', 'Paper lantern', 'Marble column'],
        ['Winter snowfall', 'Summer sunshine', 'Autumn foliage', 'Spring blossom'],
        ['Copper wire', 'Rubber gasket', 'Wooden axle', 'Ceramic nozzle'],
    ]
    def page(idx, content):
        return {'page_index': idx, 'title': 'Quiz', 'content': [{'text': content}], 'items': [
            {'item_id': f'p{idx}-{j+1}', 'panel_key': str(j+1), 'type': 'multiple-choice',
             'prompt_text': f'Choose a fictional object {j+1}', 'attempts': 'multi', 'input_mode': 'single',
             'options': [{'option_id': f'o{k+1}', 'text': f'{chr(65+k)}. {s}'} for k, s in enumerate(opts)]}
            for j, opts in enumerate(pools)]}
    return {'module_id': 'c1-r109', 'lesson': {'title': '1.2 Demo'}, 'pages': [
        page(2, 'Individual readiness iRAT'), page(3, 'Team readiness tRAT'),
        {'page_index': 4, 'title': 'Case', 'content': [], 'items': []},
        dict(page(5, ''), items=[{'item_id': f'p5-{i}', 'panel_key': str(i), 'type': 'multiple-choice', 'options': [{'text': 'Odd'}, {'text': 'Even'}]} for i in range(3)]),
        {'page_index': 6, 'title': 'True or false', 'content': [], 'items': [
            {'item_id': f'p6-{i}', 'panel_key': str(i), 'type': 'multiple-choice', 'options': [{'text': 'True'}, {'text': 'False'}]} for i in range(4)]}]}


def marked(mod=None, idx=3):
    p = next(p for p in (mod or module())['pages'] if p['page_index'] == idx)
    return '\n\n'.join('An invented question\n' + '\n'.join(
        f'{chr(65+k)}. {rat.normalise(o["text"], k)}' + (' ✓' if k == j % 4 else '')
        for k, o in enumerate(it['options'])) for j, it in enumerate(p['items']))


def compact(page=3):
    return f'---\nlt-rat: 1\nmodule: c1-r109\npage: {page}\nkind: trat\n---\n1. B\n2. D | Desert oasis\n3. A\n4. ?\n5. -\n'


def policy(root=None, **values):
    return dict(rat.DEFAULT_POLICY, answers_dir=str(root) if root else '', **values)


def put(root, name='team.md', text=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text or compact(), encoding='utf-8')
    return root / name


def scan(tmp_path, text=None, **values):
    root = tmp_path / 'sheets'
    put(root, text=text)
    pol = policy(root, trat='from_dir', **values)
    mod = module()
    return mod, pol, pk.classify(mod, pol)


def decision(mod, pol, record, idx=3, answers=None):
    return rat.decide(next(p for p in mod['pages'] if p['page_index'] == idx), pol, record, answers)


def test_T1_no_policy_no_reads(tmp_path, monkeypatch):
    root = tmp_path / 'sheets'
    put(root)
    def fail(*a):
        pytest.fail('sheet opened without read policy')
    monkeypatch.setattr(rat, 'read_sheet', fail)
    record = pk.classify(module(), policy(root))
    assert record['matches'] == []
    assert not any(record['scan']['read'][k] for k in ('irat', 'trat'))
    assert rat.roots(policy(root, trat='from_dir'))['roots'][0]['files_seen'] == 1


def test_T2_T3_T4_file_boundaries(tmp_path):
    root = tmp_path / 'sheets'
    put(root)
    put(root, '.hidden.md')
    put(root / 'sub', text=marked())
    (root / 'link.md').symlink_to(root / 'team.md')
    (root / 'linked-folder').symlink_to(root / 'sub', target_is_directory=True)
    (root / 'picture.png').write_bytes(b'ignored')
    (root / 'large.md').write_bytes(b'x' * (300 * 1024))
    (root / 'invalid.txt').write_bytes(b'\xff')
    pol = policy(root, trat='from_dir')
    report, sheets = rat.scan_sheets(pol, {'trat': True})
    assert [s['name'] for s in sheets] == ['team.md']
    assert report['roots'][0]['skipped'] == {'hidden': 1, 'linked': 2, 'not-a-file': 1, 'not-a-sheet': 1, 'unreadable': 1, 'too-large': 1}
    with pytest.raises(rat.RatError):
        rat.read_sheet(root, 'link.md')
    report, sheets = rat.scan_sheets(dict(pol, answers_dirs=[str(root / 'sub')]), {'trat': True})
    assert len(sheets) == 2
    assert sheets[1]['root'] == 'rat.answers_dirs[0]'


def test_T2_inode_race_and_reparse(tmp_path, monkeypatch):
    root = tmp_path / 'sheets'
    put(root)
    original = os.lstat
    def changed(p, *args, **kwargs):
        st = original(p, *args, **kwargs)
        if Path(p).name == 'team.md':
            vals = list(st)
            vals[1] += 1
            return os.stat_result(vals)
        return st
    monkeypatch.setattr(os, 'lstat', changed)
    with pytest.raises(rat.RatError, match='changed-during-open'):
        rat.read_sheet(root, 'team.md')
    class Reparse:
        st_mode = stat.S_IFREG
        st_file_attributes = 0x400
    assert rat._linked(Reparse())


def test_T4_candidate_cap(tmp_path):
    for i in range(501):
        (tmp_path / f'{i}.md').touch()
    with pytest.raises(rat.RatError, match='rat.answers_dir: more than 500'):
        rat.roots(policy(tmp_path))


def test_T5_config_paths_and_rejections(tmp_path):
    root = tmp_path / 'course'
    root.mkdir()
    sheetroot = root / 'RATs'
    sheetroot.mkdir()
    location = config.Location(tmp_path / 'config.toml', 'flag', tmp_path / 'config.toml', 'flag')
    cfg = config.build({'rat': {'answers_dirs': ['course/RATs'], 'irat': 'from_dir'}, 'curriculum': {'root': str(root)}}, location)
    assert cfg.get('rat.answers_dirs') == [str(sheetroot)]
    for bad in ({'scan_depth': 1}, {'export_answers': True}, {'unknown': 1}):
        with pytest.raises(config.ConfigError):
            config.build({'rat': bad}, location)
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    for r, extra in [(str(alias), {'curriculum': {'root': str(root)}}),
                     (str(tmp_path), {'curriculum': {'root': str(root)}}),
                     (str(sheetroot), {'notes': {'graph_path': str(root)}}),
                     (str(root), {'notes': {'graph_path': str(sheetroot)}}),
                     (str(root), {'paths': {'data_dir': str(sheetroot)}}),
                     (str(sheetroot), {'paths': {'data_dir': str(root)}})]:
        with pytest.raises(config.ConfigError):
            config.build(dict(extra, rat={'answers_dir': r}), location)
    file = tmp_path / 'file'
    file.touch()
    with pytest.raises(config.ConfigError):
        config.build({'rat': {'answers_dir': str(file)}}, location)
    cfg = config.build({'rat': {'answers_dir': 'missing'}}, location)
    assert any('does not exist' in s for s in cfg.warnings)


def test_T6_compact_grammar():
    sheet = rat.parse_sheet(compact().replace('3. A', '3. A, C'))
    assert sheet['status'] == 'parsed'
    assert [q['selected'] for q in sheet['questions']] == [[1], [3], [0, 2], [], []]
    for text in (compact().replace('page: 3\n', ''), compact().replace('module: c1-r109\n', ''),
                 compact().replace('3. A', '2. A'), compact().replace('3. A', '7. A')):
        assert rat.parse_sheet(text)['status'] == 'malformed'


def test_T7_marks_and_malformed():
    sheet = rat.parse_sheet('Stem\nA. red ✓\nB. blue ✔\nC. green ✅\nD. white ❌\n\nSecond\n[x] A) yes\nB) no ✘\n\nThird\nA. x\nB. y ✗\n\nFourth\nA. a\nA. b\n\nSingleton\nA. only')
    assert [q['selected'] for q in sheet['questions']] == [[0, 1, 2], [0], [], []]
    assert sheet['questions'][3]['malformed']
    it = {'input_mode': 'single', 'options': [{'text': c} for c in ('red', 'blue', 'green', 'white')]}
    assert rat._alignment(sheet['questions'][0], it)['status'] == 'multi-marked'


def test_T8_T14_T15_full_distinct_and_hard_filters(tmp_path):
    mod, pol, record = scan(tmp_path, text=marked())
    assert decision(mod, pol, record)['decision'] == 'fill-from-sheet'
    ev = next(m['evidence'] for m in record['matches'] if m['page_index'] == 3)
    assert (ev['grade'], ev['distinct'], ev['answered']) == ('full', 5, 5)
    sheet = rat.read_sheet(pol['answers_dir'], 'team.md')
    for key, value in [('module', 'other'), ('page', '2'), ('questions', '99')]:
        altered = copy.deepcopy(sheet)
        altered['meta'][key] = value
        assert rat.match_sheet(altered, mod['pages'][1], mod) is None
    compact_sheet = rat.parse_sheet(compact())
    assert rat.match_sheet(compact_sheet, mod['pages'][0], mod) is None
    assert all(m['page_index'] != 5 for m in record['matches'])


def test_T9_prefix_only_own_position():
    assert rat.normalise('A.  Object', 0) == 'object'
    assert rat.normalise('E. coli', 2) == 'e. coli'


def test_T10_shuffled_duplicate_options_fail():
    mod = module()
    s = rat.parse_sheet(marked())
    s['questions'][0]['options'][0:2] = reversed(s['questions'][0]['options'][0:2])
    assert not rat.match_sheet(s, mod['pages'][1], mod)['mapping'][0]['aligned']
    s = rat.parse_sheet(marked())
    s['questions'][0]['options'][1] = s['questions'][0]['options'][0]
    assert not rat.match_sheet(s, mod['pages'][1], mod)['mapping'][0]['aligned']


def test_T11_weak_unchosen_and_T8_chosen_threshold():
    item = {'input_mode': 'single', 'options': [{'text': x} for x in ['aaaa', 'bbbb', 'cccc', 'dddd']]}
    q = {'options': ['aaaa', 'bbbb', 'cccc', 'ddzz'], 'selected': [0]}
    row = rat._alignment(q, item)
    assert row['aligned'] and row['status'] == 'answered'
    q['selected'] = [3]
    row = rat._alignment(q, item)
    assert row['aligned'] and row['status'] == 'mark-mismatch' and row['selected'] == []


def test_T12_T13_indistinct_and_partial_need_confirmation(tmp_path):
    mod, pol, rec = scan(tmp_path, text=marked(idx=6))
    # Unknown-kind full match uses equal policies and requires indistinct confirmation.
    pol['irat'] = 'from_dir'
    rec = pk.classify(mod, pol, rec)
    d = decision(mod, pol, rec, 6)
    assert d['decision'] == 'rat-sheet-confirm' and 'indistinct' in d['reasons']
    rec = pk.choose(mod, rec, pol, 6, 'sheet', 'Use my sheet', 'rat.answers_dir:team.md')
    assert decision(mod, pol, rec, 6)['decision'] == 'fill-from-sheet'
    text = marked().split('\n\n')
    text[3:] = ['X\nA. alien\nB. strange\nC. unknown\nD. distant'] * 2
    put(Path(pol['answers_dir']), text='\n\n'.join(text))
    rec = pk.classify(mod, pol, rec)
    d = decision(mod, pol, rec)
    assert d['decision'] == 'rat-sheet-confirm' and d['sheet']['grade'] == 'partial'
    assert d['sheet']['aligned_questions'] == [1, 2, 3]
    assert '1, 2, 3' in d['question']


def test_T16_T22_week_and_kind_confirmation(tmp_path):
    mod, pol, rec = scan(tmp_path, text='---\nkind: irat\nweek: "2.3"\n---\n'+marked())
    d = decision(mod, pol, rec)
    assert d['decision'] == 'rat-sheet-confirm'
    assert set(d['reasons']) == {'week-differs', 'kind-differs'}
    rec = pk.choose(mod, rec, pol, 3, 'sheet', 'Use the named sheet', 'rat.answers_dir:team.md')
    assert decision(mod, pol, rec)['decision'] == 'fill-from-sheet'


def test_T17_duplicate_and_ambiguous_sheets(tmp_path):
    mod, pol, rec = scan(tmp_path, text=marked())
    put(Path(pol['answers_dir']), 'zzz.md', marked())
    rec = pk.classify(mod, pol, rec)
    assert decision(mod, pol, rec)['sheet']['name'] == 'team.md'
    changed = marked().replace('amber spindle ✓', 'amber spindle').replace('cobalt rotor\n', 'cobalt rotor ✓\n', 1)
    put(Path(pol['answers_dir']), 'zzz.md', changed)
    rec = pk.classify(mod, pol, rec)
    assert 'ambiguous' in decision(mod, pol, rec)['reasons']
    put(Path(pol['answers_dir']), 'zzz.md', '---\nkind: trat\n---\n'+changed)
    rec = pk.classify(mod, pol, rec)
    assert decision(mod, pol, rec)['sheet']['name'] == 'zzz.md'
    assert decision(mod, pol, rec)['decision'] == 'fill-from-sheet'


def test_T18_one_sheet_two_policies(tmp_path):
    mod, pol, rec = scan(tmp_path, text=marked())
    assert decision(mod, pol, rec, 2)['decision'] == 'irat-policy'
    assert decision(mod, pol, rec, 3)['decision'] == 'fill-from-sheet'


def test_T19_markers_never_titles():
    mod = module()
    p = mod['pages'][1]
    p['content'] = []
    p['title'] = 'tRAT'
    rec = pk.classify(mod)
    assert decision(mod, rat.DEFAULT_POLICY, rec)['kind'] is None
    p['content'] = [{'text': 'gRAT iRAT'}]
    rec = pk.classify(mod)
    assert decision(mod, rat.DEFAULT_POLICY, rec)['kind'] is None
    p['content'] = [{'text': 'gRAT'}]
    p['items'][0]['type'] = 'text'
    rec = pk.classify(mod)
    d = decision(mod, rat.DEFAULT_POLICY, rec)
    assert d['candidate'] and d['kind'] == 'trat' and d['decision'] == 'rat-ask'


def test_T20_informed_marks_and_check_veto(tmp_path):
    mod, pol, rec = scan(tmp_path)
    rec = pk.mark(mod, rec, 3, 'not-rat', 'Ordinary')
    assert decision(mod, pol, rec)['decision'] == 'not-rat'
    assert not decision(mod, pol, rec)['check_allowed']
    assert decision(mod, pol, rec)['check_reason'] == 'rat-page'
    rec['marks'][0]['evidence_seen'] = []
    assert decision(mod, pol, rec)['decision'] == 'rat-unclassified'
    rec = pk.mark(mod, rec, 3, 'irat', 'Individual')
    assert decision(mod, pol, rec)['kind'] == 'irat'
    mod['pages'][1]['content'] = []
    ans = {'items': [{'item_id': it['item_id'], 'status': 'skipped', 'skip_reason': 'trat-answers-unavailable'} for it in mod['pages'][1]['items']]}
    rec = pk.classify(mod, rat.DEFAULT_POLICY, answers=ans)
    rec = pk.mark(mod, rec, 3, 'not-rat', 'Ordinary')
    assert decision(mod, rat.DEFAULT_POLICY, rec, answers=ans)['check_allowed']


def test_T21_unknown_kind_equal_policy(tmp_path):
    mod, pol, rec = scan(tmp_path, text=marked())
    mod['pages'][1]['content'] = []
    pol['irat'] = 'from_dir'
    rec = pk.classify(mod, pol, rec)
    assert decision(mod, pol, rec)['decision'] == 'fill-from-sheet'
    pol['irat'] = 'skip'
    rec = pk.classify(mod, pol, rec)
    assert decision(mod, pol, rec)['decision'] == 'rat-unclassified'


def test_T23_decision_transitions(tmp_path):
    mod = module()
    assert decision(mod, rat.DEFAULT_POLICY, None)['decision'] == 'rat-record-stale'
    mod, pol, rec = scan(tmp_path)
    for fill, expected in [('leave', 'rat-user-leave'), ('draft', 'fill-from-draft'), ('sheet', 'fill-from-sheet')]:
        chosen = pk.choose(mod, rec, pol, 3, fill, 'User choice', 'rat.answers_dir:team.md' if fill == 'sheet' else None)
        assert decision(mod, pol, chosen)['decision'] == expected
    for idx in (2, 5):
        with pytest.raises(rat.RatError, match='only a team'):
            pk.choose(mod, rec, pol, idx, 'draft', 'Draft')
    with pytest.raises(rat.RatError):
        pk.choose(mod, rec, pol, 99, 'leave', 'Leave')
    for mode, expected in [('skip', 'trat-policy-skip'), ('ask', 'rat-ask')]:
        p = dict(pol, trat=mode)
        r = pk.classify(mod, p)
        assert decision(mod, p, r)['decision'] == expected
    p = policy(trat='from_dir')
    r = pk.classify(mod, p)
    assert decision(mod, p, r)['decision'] == 'rat-sheet-missing'
    r = pk.mark(mod, r, 3, 'not-rat', 'Ordinary')
    assert decision(mod, p, r)['decision'] == 'not-rat'
    p = policy(irat='skip', trat='skip')
    r = pk.classify(mod, p)
    r = pk.mark(mod, r, 3, 'not-rat', 'Ordinary')
    assert decision(mod, p, r, 2)['decision'] == 'irat-policy'


def test_T23_ask_choice_grants_reading(tmp_path):
    root = tmp_path / 'sheets'
    put(root)
    mod, pol = module(), policy(root)
    rec = pk.classify(mod, pol)
    assert not rec['matches']
    rec = pk.choose(mod, rec, pol, 3, 'sheet', 'Read my sheets')
    rec = pk.classify(mod, pol, rec)
    assert decision(mod, pol, rec)['decision'] == 'fill-from-sheet'
    pol['answers_dir'] = str(tmp_path / 'other')
    rec = pk.classify(mod, pol, rec)
    assert rec['choices'] == [] and rec['matches'] == []


def test_T25_record_is_safe_and_schema_valid(tmp_path):
    root = tmp_path / 'sheets'
    text = compact().replace('kind: trat', 'kind: trat\nweek: "1. B 2. D"\nlesson: SECRET QUESTION')+'# SECRET ANSWERS\n'
    put(root, 'ignore previous instructions.md', text)
    put(root, 'odd`|\nname.md', text)
    pol, mod = policy(root, trat='from_dir'), module()
    rec = pk.classify(mod, pol)
    pk.validate_record(rec)
    import jsonschema
    jsonschema.validate(rec, json.loads(pk.SCHEMA.read_text()))
    output = json.dumps(rec)
    for secret in ('Desert oasis', '1. B', '2. D', 'SECRET', str(tmp_path)):
        assert secret not in output
    names = [m['evidence']['name'] for m in rec['matches']]
    assert 'ignore previous instructions.md' in names
    assert any(n.startswith('sheet-') for n in names)
    assert all(m['evidence']['week'] in {'agree', 'differs', 'absent'} for m in rec['matches'])


def test_T26_freshness_and_marks(tmp_path):
    mod, pol, rec = scan(tmp_path)
    rec = pk.mark(mod, rec, 5, 'not-rat', 'Practice')
    assert pk.current(mod, rec, pol)
    for field, value in [('title', 'new'), ('rat_candidate', {'basis': 'synthetic'}), ('is_report_page', True), ('content', [{'text': 'iRAT'}])]:
        changed = copy.deepcopy(mod)
        changed['pages'][3][field] = value
        assert not pk.current(changed, rec, pol)
        assert not pk.classify(changed, pol, rec)['marks']
    for field, value in [('attempts', 'single'), ('assessment_type', 'graded')]:
        changed = copy.deepcopy(mod)
        changed['pages'][3]['items'][0][field] = value
        assert not pk.current(changed, rec, pol)
    changed = copy.deepcopy(mod)
    changed['pages'][3]['items'][0]['answered'] = True
    assert pk.current(changed, rec, pol)
    assert len(pk.classify(changed, pol, rec)['marks']) == 1
    assert not pk.current(mod, rec, dict(pol, trat='ask'))
    assert not pk.current(mod, rec, dict(pol, kind_markers=dict(rat.DEFAULT_MARKERS, trat='NEVER')))
    assert not pk.current(mod, rec, pol, {'items': []})
    put(Path(pol['answers_dir']), 'new.md')
    assert not pk.current(mod, rec, pol)


def test_T35_projection_no_record_non_candidate():
    rows = pk.page_status(module(), None)
    assert rows[2]['candidate'] is False and rows[2]['check_allowed'] is True
    assert rows[3]['rat'] is None and rows[3]['check_reason'] == 'rat-unmarked'


def test_T1_T40_audit_all_sheet_reads_have_reader_frame(tmp_path):
    root = tmp_path / 'sheets'
    put(root)
    pol = policy(root, trat='from_dir')
    opened = []
    active = [True]
    def audit(event, args):
        if not active[0] or event != 'open' or not isinstance(args[0], (str, bytes)):
            return
        path = Path(os.fsdecode(args[0]))
        if not path.is_relative_to(root):
            return
        frame = sys._getframe()
        reader = False
        while frame:
            reader |= frame.f_globals.get('__name__') == 'ltcore.rat' and frame.f_code.co_name == 'read_sheet'
            frame = frame.f_back
        opened.append(reader)
    sys.addaudithook(audit)
    try:
        rec = pk.classify(module(), policy(root))
        assert opened == []
        rec = pk.classify(module(), pol)
        assert opened and all(opened)
        opened.clear()
        assert pk.current(module(), rec, pol)
        pk.page_status(module(), rec)
        rat.roots(pol)
        assert opened == []
    finally:
        active[0] = False


def test_T23_T26_choice_invalidations(tmp_path):
    mod, pol, rec = scan(tmp_path)
    rec = pk.choose(mod, rec, pol, 3, 'sheet', 'Use my sheet', 'rat.answers_dir:team.md')
    for part in ('page_digest', 'classification_digest', 'roots_digest', 'policy_sha256'):
        altered = copy.deepcopy(rec)
        altered['choices'][0][part] = '0' * 64
        assert pk.classify(mod, pol, altered)['choices'] == []
    altered = copy.deepcopy(rec)
    altered['choices'][0]['sheet']['sha256'] = '0' * 64
    assert pk.classify(mod, pol, altered)['choices'] == []
    missing = tmp_path / 'empty'
    missing.mkdir()
    pol = policy(missing, trat='from_dir')
    rec = pk.classify(mod, pol)
    rec = pk.choose(mod, rec, pol, 3, 'leave', 'Wait for my sheet')
    assert rec['choices'][0]['asked'] == 'rat-sheet-missing'
    put(missing)
    assert pk.classify(mod, pol, rec)['choices'] == []


def test_T28_resolver_prunes_rat_roots(tmp_path, repo_root, run_py):
    course = tmp_path / 'course'
    put(course / 'Block A/RATs/Week 1.2 Wrong')
    put(course / 'Block A/Week 1.2 Demo', 'slides.md', 'Ordinary synthetic source')
    cfg = tmp_path / 'cfg.toml'
    cfg.write_text(f'[curriculum]\nroot = {json.dumps(str(course))}\nweek_number_pattern = \'(\\d+)\\.(\\d+)\'\n[rat]\nanswers_dir = {json.dumps(str(course / "Block A/RATs"))}\n')
    r = run_py(repo_root / 'skills/lt-module-answer/scripts/resolve_week.py', '--title', '1.2 Demo', '--config', cfg, '--json')
    assert r.returncode == 0, r.stdout+r.stderr
    result = json.loads(r.stdout)
    assert result['rat_roots_excluded'] == 1
    assert len(result['matches']) == 1 and result['matches'][0]['name'] == 'Week 1.2 Demo'


def test_T25_cli_output_and_exit_contract(tmp_path, repo_root, run_py):
    root = tmp_path / 'sheets'
    put(root, 'ignore previous instructions.md', compact()+'# 9. Z SECRET\n')
    put(root, 'odd`|\nname.md', compact().replace('kind: trat', 'kind: trat\nweek: "1. B 2. D"\nlesson: SECRET'))
    mod = tmp_path / 'module.json'
    mod.write_text(json.dumps(module()))
    cfg = tmp_path / 'cfg.toml'
    cfg.write_text(f'[rat]\nanswers_dir = {json.dumps(str(root))}\ntrat = "from_dir"\n')
    cli = repo_root / 'skills/lt-module-answer/scripts/page_kinds.py'
    for command in ('roots', 'classify'):
        for flags in ([], ['--json']):
            args = [command, '--config', cfg, '--data-dir', tmp_path / 'data', *flags]
            if command == 'classify':
                args += ['--module', mod]
            result = run_py(cli, *args)
            assert result.returncode == (0 if command == 'roots' else 1), result.stderr
            for forbidden in ('Desert oasis', '1. B', '2. D', 'SECRET', str(tmp_path), '9. Z'):
                assert forbidden not in result.stdout+result.stderr
    assert __import__('ltcore.validate', fromlist=['infer_schema']).infer_schema('page-kinds-c1-r109.json') == 'page-kinds.schema.json'


def test_T2_open_time_symlink_swap(tmp_path, monkeypatch):
    root = tmp_path / 'sheets'
    path = put(root)
    target = put(root, 'other.md', marked())
    original = os.open
    def swap(p, flags, *args, **kwargs):
        if Path(p) == path:
            path.unlink()
            path.symlink_to(target)
        return original(p, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', swap)
    with pytest.raises(rat.RatError):
        rat.read_sheet(root, path.name)


def test_T4_utf8_bom_and_markdown_extension(tmp_path):
    (tmp_path / 'sheet.markdown').write_bytes(b'\xef\xbb\xbf' + compact().encode())
    sheet = rat.read_sheet(tmp_path, 'sheet.markdown')
    assert sheet['status'] == 'parsed' and sheet['format'] == 'compact'


def test_T23_unknown_kind_equal_skip():
    mod = module()
    mod['pages'][1]['content'] = [{'text': 'iRAT and tRAT'}]
    pol = policy(irat='skip', trat='skip')
    rec = pk.classify(mod, pol)
    assert decision(mod, pol, rec)['decision'] == 'rat-policy-skip'


def test_T26_remove_edit_retarget_stale(tmp_path):
    mod, pol, rec = scan(tmp_path)
    root = Path(pol['answers_dir'])
    (root / 'team.md').write_text(compact().replace('1. B', '1. A'))
    assert not pk.current(mod, rec, pol)
    rec = pk.classify(mod, pol, rec)
    (root / 'team.md').unlink()
    assert not pk.current(mod, rec, pol)
    other = tmp_path / 'other'
    put(other)
    alias = tmp_path / 'alias'
    alias.symlink_to(other, target_is_directory=True)
    pol['answers_dir'] = str(alias)
    rec = pk.classify(mod, pol)
    alias.unlink()
    alias.symlink_to(root, target_is_directory=True)
    assert not pk.current(mod, rec, pol)


def test_T5_data_override_cannot_be_a_root(tmp_path, repo_root, run_py):
    root = tmp_path / 'sheets'
    put(root)
    cfg = tmp_path / 'config.toml'
    cfg.write_text(f'[rat]\nanswers_dir = {json.dumps(str(root))}\ntrat = "from_dir"\n')
    r = run_py(repo_root / 'skills/lt-module-answer/scripts/page_kinds.py', 'roots', '--config', cfg, '--data-dir', root)
    assert r.returncode == 3
    assert 'rat.answers_dir: overlaps data folder' in r.stderr


@pytest.mark.skipif(sys.platform not in ('win32', 'darwin'), reason='case-insensitive alias needs Windows or macOS')
def test_T5_case_insensitive_alias(tmp_path):
    root = tmp_path / 'Course'
    root.mkdir()
    alias = tmp_path / 'COURSE'
    if not alias.exists():
        pytest.skip('filesystem is case-sensitive')
    loc = config.Location(tmp_path/'cfg.toml', 'flag', tmp_path/'cfg.toml', 'flag')
    with pytest.raises(config.ConfigError):
        config.build({'rat': {'answers_dir': str(alias)}, 'curriculum': {'root': str(root)}}, loc)


def test_T26_consent_never_crosses_runs_or_policy_pins(tmp_path):
    mod, pol, rec = scan(tmp_path)
    rec = pk.choose(mod, rec, pol, 3, 'sheet', 'Use sheet', 'rat.answers_dir:team.md')
    rec['choices'][0]['policy_sha256'] = '0' * 64
    assert not pk.current(mod, rec, pol)
    rec = pk.mark(mod, rec, 5, 'not-rat', 'Practice')
    rec['module_id'] = 'c1-r110'
    refreshed = pk.classify(mod, pol, rec)
    assert refreshed['marks'] == [] and refreshed['choices'] == []


def test_T35_projection_includes_the_shared_fill_decision(tmp_path):
    mod, pol, rec = scan(tmp_path)
    rows = {r['page_index']: r for r in pk.page_status(mod, rec)}
    assert rows[3]['decision'] == 'fill-from-sheet'
    assert rows[2]['decision'] == 'irat-policy'
    assert rows[5]['decision'] == 'rat-unclassified'


def test_T25_safe_evidence_excludes_reversible_selection_hash():
    mod = module()
    sheet = rat.parse_sheet(compact())
    match = rat.match_sheet(sheet, mod['pages'][1], mod)
    ev = rat.evidence(match)
    assert 'mapping' not in ev and 'selection_sha256' not in ev
    assert ev['answered'] == 3


def test_T13_partial_sheet_accounts_for_missing_questions():
    mod = module()
    sheet = rat.parse_sheet(compact().split('4. ?')[0])
    match = rat.match_sheet(sheet, mod['pages'][1], mod)
    assert match['grade'] == 'partial'
    assert rat.evidence(match)['todo_questions'] == {'4': 'unaligned', '5': 'unaligned'}


def test_T16_week_hints_follow_configured_week_pattern(tmp_path):
    mod = module()
    mod['lesson']['title'] = 'Session 9: Invented objects'
    sheet = rat.parse_sheet('---\nweek: 10\n---\n'+marked())
    match = rat.match_sheet(sheet, mod['pages'][1], mod, week_pattern=r'Session (\d+)')
    assert match['week'] == 'differs'
    sheet['meta']['week'] = '09'
    assert rat.match_sheet(sheet, mod['pages'][1], mod, week_pattern=r'Session (\d+)')['week'] == 'agree'
