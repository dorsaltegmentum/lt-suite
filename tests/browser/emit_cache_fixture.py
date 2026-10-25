# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generate synthetic fill scripts for checking browser cache eligibility across installation paths."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
BASE = Path(sys.argv[1])
sys.path[:0] = [str(REPO / 'shared'), str(REPO / 'tests/fill')]
from fillkit import Kit, answers, drafted, module, text_pages
from ltcore import config, ltjs

base = BASE / 'generated'
base.mkdir(exist_ok=True)
kit = Kit(base)
config_path = kit.config('[lt]\norigin="https://lt.example.invalid"\n[browser]\njs_tmp_dir=' + json.dumps(str(base / 'rendered')) + '\n')
rc, pay, output = kit.payload(module(text_pages(1)), answers([drafted('p1-10')]))
assert rc == 0, output
result = kit.emit('scripts', '--out', 'out', '--stub')
assert result.returncode == 0, result.stdout + result.stderr
source = Path(kit.read('out/scripts.json')['calls'][0]['source'])
if not source.is_absolute():
    source = base / source
cfg = config.load(config_path)
ordinary = ltjs.render(str(source), stub=True, cfg=cfg, out_dir=base / 'rendered')
scripts = base / 'installation' / 'scripts'
browser = scripts / 'browser'
browser.mkdir(parents=True, exist_ok=True)
relocated = browser / source.name
rc, pay, output = kit.payload(module(text_pages(1)), answers([drafted('p1-10')]), out=str(browser))
assert rc == 0, output
result = kit.emit('scripts', '--out', str(browser), '--stub', '--no-render')
assert result.returncode == 0, result.stdout + result.stderr
assert relocated.is_file()
with patch.object(ltjs, 'skill_scripts_dir', return_value=scripts):
    relocated_info = ltjs.render(str(relocated), stub=True, cfg=cfg, out_dir=base / 'rendered')
(BASE / 'generated.json').write_text(json.dumps({'ordinary': ordinary, 'relocated': relocated_info}, indent=2), encoding='utf-8', newline='\n')
print(json.dumps({'ordinary_cacheable': ordinary['cacheable'], 'relocated_cacheable': relocated_info['cacheable']}))
