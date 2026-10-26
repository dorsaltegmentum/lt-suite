# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise payload identity and draft text through the public fill commands."""
import copy
import hashlib
import json

import pytest

from ltcore import validate
from fillkit import answers, drafted, module, text_pages


PHRASES = ['this draft', 'in the draft', 'in this draft', 'not checked in eTG',
           'not yet verified in eTG', 'TODO', 'T' + 'BD', 'FIXME', 'placeholder', '[T1]']


def prepare(kit, text='New answer.'):
    mod = module(text_pages(1, 3))
    mp = kit.write('module.json', mod)
    ans = answers([drafted(i['item_id'], segments=[{'kind': 'claim', 'text': text,
                     'provenance_refs': ['p1']}]) for i in mod['pages'][0]['items']])
    ans['source_module']['sha256'] = hashlib.sha256(mp.read_bytes()).hexdigest()
    kit.write('answers.json', ans)
    return mod, ans


def payload(kit, folder, text):
    if folder in ('out', 'other'):
        folder = kit.run_dir().relative_to(kit.tmp).as_posix() + '/fill-js-' + folder
    prepare(kit, text)
    result = kit.emit('payload', '--module', 'module.json', '--answers', 'answers.json',
                      '--out', folder, '--force')
    assert result.returncode == 0, result.stdout + result.stderr
    path = kit.tmp / folder / 'fill-payload.json'
    kit.write(folder + '/scripts.json', {'payload': str(path)})
    pay = json.loads(path.read_text(encoding='utf-8'))
    from fillkit import SCRIPTS
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import emit_fill
    for page in pay['pages'].values():
        for job in page['jobs']:
            emit_fill.job_hash(job)
    return path, pay


def receipts(kit, path, pay, *, short=False):
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    pin = {'source_payload_sha256': '[BLOCKED: Base64 encoded data]',
           'source_payload_sha16': sha[:16]} if short else {'source_payload_sha256': sha}
    jobs = pay['pages']['1']['jobs']
    kit.write('fill.json', dict(ok=True, module='c1-r101', page=1, save_signal='fallback',
              items=[{'item': j['item'], 'result': 'written', 'h': j['h']} for j in jobs], **pin))
    kit.write('verify.json', dict(ok=True, module='c1-r101', fresh_load=True,
              observed_at='2000-01-01T00:00:00Z',
              items=[[j['item'], 'ok', 3, j['h']] for j in jobs], **pin))


def record(kit, *extra):
    return kit.emit('record', '--module', 'module.json', '--fill', 'fill.json',
                    '--verify', 'verify.json', *extra)


@pytest.mark.parametrize('short', [False, True])
def test_record_discovers_second_payload_for_three_changed_answers(kit, short):
    run = kit.run_dir().relative_to(kit.tmp).as_posix()
    payload(kit, run + '/fill-js', 'Old answer.')
    path, pay = payload(kit, run + '/fill-js-p11', 'Corrected answer.')
    receipts(kit, path, pay, short=short)
    result = record(kit)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = kit.read(run + '/receipts.json')['receipts']
    assert [r['expected_hash'] for r in rows] == [j['h'] for j in pay['pages']['1']['jobs']]
    field = 'source_payload_sha16' if short else 'source_payload_sha256'
    assert all(r['payload_binding']['fill'] == field for r in rows)


@pytest.mark.parametrize('pin', ['absent', 'wrong', 'full-conflict', 'ambiguous'])
def test_record_refuses_unbound_payload_without_writing_receipts(kit, pin, monkeypatch, emit_mod):
    path, pay = payload(kit, 'out', 'Answer.')
    receipts(kit, path, pay)
    rec = kit.read('fill.json')
    if pin == 'absent':
        rec.pop('source_payload_sha256')
    elif pin == 'full-conflict':
        rec['source_payload_sha16'] = rec['source_payload_sha256'][:16]
        rec['source_payload_sha256'] = '0' * 64
    elif pin == 'ambiguous':
        other, _ = payload(kit, 'other', 'Different.')
        rec['source_payload_sha16'] = 'a' * 16
        rec['source_payload_sha256'] = '[BLOCKED: Base64 encoded data]'
        monkeypatch.setattr(emit_mod, 'sha256', lambda p: 'a' * 16 + ('1' if p == path else '2') * 48)
        with pytest.raises(emit_mod.Refusal, match='ambiguous'):
            emit_mod.bind_payload(rec, [path, other])
        return
    else:
        rec['source_payload_sha256'] = '0' * 64
    kit.write('fill.json', rec)
    result = record(kit, '--payload', str(path))
    assert result.returncode == 3, result.stdout + result.stderr
    assert str(path) in result.stderr
    assert not (kit.run_dir() / 'receipts.json').exists()


def test_record_refuses_conflicting_expected_hash_without_supersede(kit):
    path, pay = payload(kit, 'out', 'Answer.')
    receipts(kit, path, pay)
    run = kit.run_dir().relative_to(kit.tmp).as_posix()
    old = {'item_id': 'p1-10', 'expected_hash': 1, 'observed_hash': 1,
           'observed_at': '2000-01-01T00:00:00Z'}
    kit.write(run + '/receipts.json', {'receipts': [old]})
    result = record(kit, '--payload', str(path))
    assert result.returncode == 3
    assert '--supersede' in result.stderr
    assert kit.read(run + '/receipts.json')['receipts'] == [old]


def test_record_supersede_archives_replaced_rows_with_reason(kit):
    path, pay = payload(kit, 'out', 'Answer.')
    receipts(kit, path, pay)
    run = kit.run_dir().relative_to(kit.tmp).as_posix()
    old = {'item_id': 'p1-10', 'expected_hash': 1, 'observed_hash': 1,
           'observed_at': '2000-01-01T00:00:00Z'}
    kit.write(run + '/receipts.json', {'receipts': [old]})
    result = record(kit, '--payload', str(path), '--supersede')
    assert result.returncode == 0, result.stdout + result.stderr
    rows = kit.read(run + '/receipts.json')['receipts']
    assert len(rows) == 3 and rows[0]['expected_hash'] != 1
    archive = kit.read(run + '/receipts-superseded.json')['receipts']
    assert archive[0]['replaced'] == old
    assert archive[0]['reason']
    assert record(kit, '--payload', str(path), '--supersede').returncode == 0
    assert len(kit.read(run + '/receipts-superseded.json')['receipts']) == 1


@pytest.mark.parametrize('phrase', PHRASES)
def test_draft_status_refused_in_segments_and_allowed_in_uncertainties(kit, phrase):
    mod, ans = prepare(kit)
    ctx = validate._Ctx(base_dir=kit.tmp, module=mod)
    original = copy.deepcopy(ans)
    ans['items'][0]['segments'][0]['text'] = phrase.swapcase()
    assert any('meta-text' in e for e in validate.rails_answers(ans, ctx))
    original['items'][0]['uncertainties'] = [phrase]
    assert not any('meta-text' in e for e in validate.rails_answers(original, ctx))


@pytest.mark.parametrize('text', ['Check bedside glucose.', 'The QT interval was not checked for prolongation.'])
def test_clinical_checks_pass_meta_text_rail(kit, text):
    mod, ans = prepare(kit, text)
    assert not any('meta-text' in e for e in validate.rails_answers(ans, validate._Ctx(base_dir=kit.tmp, module=mod)))
    assert kit.emit('payload', '--module', 'module.json', '--answers', 'answers.json', '--out', 'out').returncode == 0


@pytest.mark.parametrize('phrase', PHRASES)
def test_payload_refuses_draft_status_html(kit, phrase):
    prepare(kit, phrase)
    result = kit.emit('payload', '--module', 'module.json', '--answers', 'answers.json', '--out', 'out')
    assert result.returncode == 3
    assert 'meta-text' in result.stderr
    assert not (kit.tmp / 'out/fill-payload.json').exists()


def test_meta_text_patterns_extend_defaults_from_config(kit):
    prepare(kit, 'Research pending.')
    kit.config('[answers]\nmeta_text_patterns = ["research pending"]\n')
    result = kit.emit('payload', '--module', 'module.json', '--answers', 'answers.json', '--out', 'out')
    assert result.returncode == 3 and 'meta-text' in result.stderr


@pytest.mark.parametrize('mutation', ['copied-relative-path', 'different-file', 'wrong-hash'])
def test_payload_and_validator_refuse_the_same_source_binding(kit, mutation):
    mod, ans = prepare(kit)
    if mutation == 'copied-relative-path':
        ans['source_module']['path'] = '../../../captures/module.json'
    elif mutation == 'different-file':
        kit.write('other.json', mod)
        ans['source_module']['path'] = 'other.json'
    else:
        ans['source_module']['sha256'] = 'f' * 64
    ap = kit.write('answers.json', ans)
    result = validate.validate_file(ap, module=kit.tmp / 'module.json')
    binding = [e for e in result.rail_errors if 'source_module' in e]
    assert binding, result
    emitted = kit.emit('payload', '--module', 'module.json', '--answers', 'answers.json', '--out', 'out')
    assert emitted.returncode == 3
    assert binding[0] in emitted.stderr


def test_record_copies_fallback_save_diagnostics(kit):
    path, pay = payload(kit, 'out', 'Answer.')
    receipts(kit, path, pay)
    fill = kit.read('fill.json')
    diag = [{'method': 'POST', 'path_template': '/save/:id', 'status': 204, 'count': 2, 'model_sync': False}]
    fill['save_diag'] = diag
    kit.write('fill.json', fill)
    assert record(kit, '--payload', str(path)).returncode == 0
    waits = json.loads((kit.run_dir() / 'save-waits.json').read_text(encoding='utf-8'))
    assert waits['save_waits'][0]['save_diag'] == diag


@pytest.mark.parametrize('diag', [[], [{'method': 'PUT', 'path_template': '/runs/:id/data',
                           'status': 200, 'count': 1, 'model_sync': False}] * 20])
def test_record_report_export_accept_save_diagnostics(kit, diag):
    import subprocess
    import sys
    from fillkit import REPO
    run = kit.run_dir().relative_to(kit.tmp).as_posix()
    path, pay = payload(kit, run + '/fill-js', 'Answer.')
    receipts(kit, path, pay, short=True)
    fill = kit.read('fill.json')
    fill.update(save_diag=diag, waited_ms=4000)
    kit.write('fill.json', fill)
    assert record(kit).returncode == 0
    kit.write(run + '/fill-notes.json', {'approval_mode': 'manual',
              'started_at': '2000-01-01T00:00:00Z', 'ended_at': '2000-01-01T00:00:00Z'})
    result = kit.run('make_fill_report.py', '--module', 'module.json', '--answers', 'answers.json')
    assert result.returncode == 0, result.stdout + result.stderr
    report = kit.read(run + '/fill-report.json')
    assert report['save_waits'][0]['save_diag'] == diag
    assert all(row['verified'] and row['result'] == 'filled' for row in report['items'])
    script = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import export_module
from ltcore import validate
assert '/lt-module-export/scripts/_shared/' in validate.__file__.replace(chr(92), '/')
doc, path, rc = export_module.validated_input(Path(sys.argv[2]), 'fill-report', validate,
                                            Path('.'), module=Path('module.json'))
assert rc == 0, rc
assert doc['save_waits'][0]['save_diag'] == __import__('json').loads(sys.argv[3])
'''
    result = subprocess.run([sys.executable, '-c', script,
                    str(REPO / 'skills/lt-module-export/scripts'), run + '/fill-report.json',
                    json.dumps(diag)], cwd=kit.tmp, env=kit.env, capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
    for validator in [REPO / 'shared/ltcore/validate.py',
                      *sorted((REPO / 'skills').glob('*/scripts/_shared/ltcore/validate.py'))]:
        result = subprocess.run([sys.executable, str(validator), run + '/fill-report.json',
                    '--schema', 'fill-report', '--module', 'module.json'],
                    cwd=kit.tmp, env=kit.env, capture_output=True, text=True, encoding='utf-8')
        assert result.returncode == 0, result.stdout + result.stderr
    for skill, command, expected in [
        ('lt-module-answer', ['reconcile.py', 'prepare', '--module', 'module.json',
                             '--answers', 'answers.json', '--fill-report', run + '/fill-report.json'], 2),
        ('lt-suite-setup', ['build_ledger.py', '--fill-report', run + '/fill-report.json', '--dry-run'], 0),
    ]:
        result = subprocess.run([sys.executable, str(REPO / 'skills' / skill / 'scripts' / command[0]),
                                 *command[1:]], cwd=kit.tmp, env=kit.env, capture_output=True, text=True, encoding='utf-8')
        assert result.returncode == expected, result.stdout + result.stderr


@pytest.mark.parametrize('mutation', ['extra', 'missing', 'method', 'path', 'status', 'count', 'sync', 'limit'])
def test_save_diagnostic_schema_rejects_invalid_rows(mutation):
    from fillkit import REPO
    import jsonschema
    schema = json.loads((REPO / 'shared/schemas/fill-report.schema.json').read_text(encoding='utf-8'))
    diag_schema = schema['properties']['save_waits']['items']['properties'].get('save_diag')
    assert diag_schema is not None
    row = {'method': 'PUT', 'path_template': '/runs/:id/data', 'status': 200,
           'count': 1, 'model_sync': False}
    if mutation == 'extra':
        row['body'] = 'private'
    elif mutation == 'missing':
        del row['method']
    elif mutation == 'method':
        row['method'] = 3
    elif mutation == 'path':
        row['path_template'] = None
    elif mutation == 'status':
        row['status'] = '200'
    elif mutation == 'count':
        row['count'] = 0
    elif mutation == 'sync':
        row['model_sync'] = 'false'
    rows = [row] * (21 if mutation == 'limit' else 1)
    assert list(jsonschema.Draft202012Validator(diag_schema).iter_errors(rows))


def test_record_ordinary_binding_refusal_uses_neutral_prefix(kit):
    run = kit.run_dir().relative_to(kit.tmp).as_posix()
    path, pay = payload(kit, run + '/fill-js', 'Answer.')
    receipts(kit, path, pay)
    ver = kit.read('verify.json')
    ver['source_payload_sha256'] = '0' * 64
    kit.write('verify.json', ver)
    result = record(kit)
    assert result.returncode == 3
    assert 'receipt binding: payload identity' in result.stderr
    assert 'RAT receipt binding:' not in result.stderr


@pytest.mark.parametrize('outside', ['absolute', 'relative', 'symlink', 'manifest-symlink', 'explicit'])
def test_payload_discovery_confines_manifest_candidates(kit, emit_mod, outside):
    from ltcore import config, store
    run = kit.run_dir().relative_to(kit.tmp).as_posix()
    path, _ = payload(kit, run + '/fill-js-p11', 'Answer.')
    external = kit.write('outside/fill-payload.json', {'private': True})
    manifest = path.parent / 'scripts.json'
    if outside in ('absolute', 'explicit'):
        kit.write(str(manifest), {'payload': str(external)})
    elif outside == 'relative':
        kit.write(str(manifest), {'payload': '../../../../outside/fill-payload.json'})
    elif outside == 'symlink':
        link = path.parent / 'external.json'
        try:
            link.symlink_to(external)
        except OSError:
            pytest.skip('symlinks are unavailable')
        kit.write(str(manifest), {'payload': 'external.json'})
    else:
        external_manifest = kit.write('outside/scripts.json', {'payload': str(path)})
        manifest.unlink()
        try:
            manifest.symlink_to(external_manifest)
        except OSError:
            pytest.skip('symlinks are unavailable')
    st = store.Store(config.load(), data_dir=kit.data)
    candidates = emit_mod.payload_candidates(st, 'c1-r101', [external] if outside == 'explicit' else [])
    assert external.resolve() not in candidates
    assert all(p.is_relative_to(kit.run_dir().resolve()) for p in candidates)
    assert path not in candidates


def test_payload_discovery_resolves_relative_paths_from_manifest_folder(kit, emit_mod):
    from ltcore import config, store
    run = kit.run_dir().relative_to(kit.tmp).as_posix()
    path, pay = payload(kit, run + '/fill-js-p11', 'Answer.')
    kit.write(str(path.parent / 'scripts.json'), {'payload': 'fill-payload.json'})
    st = store.Store(config.load(), data_dir=kit.data)
    candidates = emit_mod.payload_candidates(st, 'c1-r101', [])
    assert path in candidates
    assert not any('fill-js-p11/fill-js-p11' in p.as_posix() for p in candidates)
    assert all(p.is_file() for p in candidates)
    receipts(kit, path, pay)
    assert record(kit).returncode == 0


def test_record_refuses_doubled_relative_sibling_manifest(kit):
    run = kit.run_dir().relative_to(kit.tmp).as_posix()
    payload(kit, run + '/fill-js', 'Old answer.')
    path, pay = payload(kit, run + '/fill-js-p11', 'Corrected answer.')
    kit.write(run + '/fill-js-p11/scripts.json', {'payload': 'fill-js-p11/fill-payload.json'})
    receipts(kit, path, pay)
    result = record(kit)
    assert result.returncode == 3, result.stderr
    assert not (kit.run_dir() / 'receipts.json').exists()


def test_supersede_refuses_a_different_verify_receipt(kit):
    path, pay = payload(kit, 'out', 'Answer.')
    receipts(kit, path, pay)
    assert record(kit, '--payload', str(path)).returncode == 0
    before = (kit.run_dir() / 'receipts.json').read_bytes()
    ver = kit.read('verify.json')
    ver['client_sig'] = 'different observation'
    kit.write('verify.json', ver)
    result = record(kit, '--payload', str(path), '--supersede')
    assert result.returncode == 3
    assert 'same verify receipt' in result.stderr
    assert (kit.run_dir() / 'receipts.json').read_bytes() == before
    assert not (kit.run_dir() / 'receipts-superseded.json').exists()


def test_payload_checks_html_added_by_a_text_cleaner(kit, emit_mod, monkeypatch):
    from ltcore import config
    path, pay = payload(kit, 'out', 'Answer.')
    def cleaner(cfg, doc, folder):
        doc['pages']['1']['jobs'][0]['html'] = '<p>eTG not checked in this draft.</p>'
        return doc, []
    monkeypatch.setattr(emit_mod, 'run_text_cleaners', cleaner)
    before = path.read_bytes()
    with pytest.raises(emit_mod.Refusal, match='meta-text'):
        emit_mod._write_payload(pay, path, config.load(), True)
    assert path.read_bytes() == before
