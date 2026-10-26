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


def test_record_discovers_legacy_relative_sibling_manifest(kit):
    run = kit.run_dir().relative_to(kit.tmp).as_posix()
    payload(kit, run + '/fill-js', 'Old answer.')
    path, pay = payload(kit, run + '/fill-js-p11', 'Corrected answer.')
    kit.write(run + '/fill-js-p11/scripts.json', {'payload': 'fill-js-p11/fill-payload.json'})
    receipts(kit, path, pay)
    result = record(kit)
    assert result.returncode == 0, result.stderr
    rows = kit.read(run + '/receipts.json')['receipts']
    assert all(r['source_payload']['path'] == str(path) for r in rows)


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
