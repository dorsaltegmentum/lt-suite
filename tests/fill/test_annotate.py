# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test annotation and grid scripts with generated images and temporary config."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT_DIR = Path("skills") / "lt-module-fill" / "scripts"


@pytest.fixture
def image_modules():
    """Skip render tests when an image dependency is unavailable."""
    image = pytest.importorskip("PIL.Image")
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy.ndimage")
    return image, numpy


@pytest.fixture
def synthetic_image(tmp_path, image_modules):
    """Create an 800 by 600 RGB image with one pink blob."""
    image, numpy = image_modules
    pixels = numpy.full((600, 800, 3), 255, dtype=numpy.uint8)
    y, x = numpy.ogrid[:600, :800]
    blob = ((x - 400) / 210.0) ** 2 + ((y - 300) / 170.0) ** 2 <= 1
    pixels[blob] = (220, 110, 160)
    path = tmp_path / "source.png"
    image.fromarray(pixels).save(path)
    return path


@pytest.fixture
def config_path(tmp_path):
    """Write only supplied keys so release defaults remain active."""
    counter = 0

    def write(*, annotate=None, fonts=None):
        nonlocal counter
        counter += 1
        path = tmp_path / f"config-{counter}.toml"
        lines = []
        for section, values in (("annotate", annotate), ("fonts", fonts)):
            if values is None:
                continue
            lines.append(f"[{section}]")
            for key, value in values.items():
                lines.append(f"{key} = {json.dumps(value)}")
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    return write


@pytest.fixture
def run_script(repo_root):
    """Run entry scripts with the shared release package on PYTHONPATH."""
    def run(name, cfg, *arguments):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(repo_root / "shared")
        env["LT_SUITE_CONFIG"] = str(cfg)
        return subprocess.run(
            [
                sys.executable,
                str(repo_root / SCRIPT_DIR / name),
                *map(str, arguments),
            ],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    return run


def assert_success(result):
    assert result.returncode == 0, (
        f"exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def find_ttf(repo_root):
    """Search bundled fonts before system fonts."""
    for directory in (repo_root / "shared" / "fonts", Path("/usr/share/fonts")):
        if directory.is_dir():
            for path in sorted(directory.rglob("*.ttf")):
                if path.is_file():
                    return path
    pytest.skip("No TrueType font found in shared/fonts or /usr/share/fonts")


def test_point_caption_uses_configured_truetype(
    repo_root,
    tmp_path,
    synthetic_image,
    image_modules,
    config_path,
    run_script,
):
    image, _ = image_modules
    font_path = find_ttf(repo_root)
    cfg = config_path(annotate={"font_path": str(font_path)})
    output = tmp_path / "annotated.png"

    result = run_script(
        "annotate_micrograph.py",
        cfg,
        synthetic_image,
        output,
        "--point",
        "structure A:400,300",
        "--caption",
        "alpha",
    )

    assert_success(result)
    assert output.is_file()
    with image.open(output) as rendered:
        assert rendered.width >= 800
        assert rendered.height > 600
        assert rendered.mode == "RGB"
    assert (
        f"font: config annotate.font_path {font_path}"
        in result.stdout + result.stderr
    )


@pytest.mark.parametrize("script", ["annotate_micrograph.py", "gridref.py"])
def test_missing_font_uses_resolver_or_pillow(
    script,
    tmp_path,
    synthetic_image,
    image_modules,
    config_path,
    run_script,
):
    cfg = config_path(
        annotate={"font_path": str(tmp_path / "missing-annotation.ttf")},
        fonts={"sans_path": str(tmp_path / "missing-sans.ttf")},
    )
    output = tmp_path / f"{Path(script).stem}-fallback.png"
    arguments = [synthetic_image, output]
    if script == "annotate_micrograph.py":
        arguments += ["--point", "structure A:400,300", "--caption", "alpha"]

    result = run_script(script, cfg, *arguments)

    assert_success(result)
    assert output.is_file()
    assert "font: config annotate.font_path" not in result.stderr
    assert (
        "font: fonts.sans " in result.stderr
        or "font: pillow-default builtin" in result.stderr
    )
    if "font: pillow-default builtin" in result.stderr:
        assert "WARNING: using pillow-default;" in result.stderr
        assert "annotate.font_path:" in result.stderr
        assert "fonts.sans:" in result.stderr


def test_configured_ink_changes_rendered_pixels(
    tmp_path,
    synthetic_image,
    image_modules,
    config_path,
    run_script,
):
    image, numpy = image_modules
    cfg = config_path(annotate={"ink": "#00FF00"})
    output = tmp_path / "green.png"

    result = run_script(
        "annotate_micrograph.py",
        cfg,
        synthetic_image,
        output,
        "--point",
        "structure A:400,300",
        "--caption",
        "alpha",
    )

    assert_success(result)
    with image.open(output) as rendered:
        pixels = numpy.asarray(rendered.convert("RGB"), dtype=numpy.int16)
    target = numpy.array((0, 255, 0), dtype=numpy.int16)
    green = numpy.all(numpy.abs(pixels - target) <= 40, axis=2)
    assert int(green.sum()) >= 50


def test_gridref_writes_source_sized_image(
    tmp_path,
    synthetic_image,
    image_modules,
    config_path,
    run_script,
):
    image, _ = image_modules
    cfg = config_path()
    output = tmp_path / "grid.png"

    result = run_script(
        "gridref.py",
        cfg,
        synthetic_image,
        output,
        "--step",
        "100",
        "--config",
        cfg,
    )

    assert_success(result)
    assert output.is_file()
    with image.open(output) as rendered:
        assert rendered.size == (800, 600)
    assert "grid step 100px" in result.stdout
    assert "font: " in result.stderr


@pytest.mark.parametrize("script", ["annotate_micrograph.py", "gridref.py"])
def test_help_exits_zero(script, config_path, run_script):
    cfg = config_path()

    result = run_script(script, cfg, "--help")

    assert_success(result)
    assert "--config" in result.stdout
    assert "--help-rules" in result.stdout


@pytest.mark.parametrize("script", ["annotate_micrograph.py", "gridref.py"])
def test_help_rules_prints_configured_rule(script, config_path, run_script):
    rule = "Name the layer, then the structure."
    cfg = config_path(annotate={"naming_rules": [rule]})

    result = run_script(script, cfg, "--help-rules", "--config", cfg)

    assert_success(result)
    assert result.stdout.splitlines() == [rule]


@pytest.mark.parametrize("script", ["annotate_micrograph.py", "gridref.py"])
def test_help_rules_prints_empty_default(script, config_path, run_script):
    cfg = config_path()

    result = run_script(script, cfg, "--help-rules")

    assert_success(result)
    assert result.stdout.strip() == "no naming rules configured"


def test_annotate_accepts_explicit_config(
    tmp_path,
    synthetic_image,
    image_modules,
    config_path,
    run_script,
):
    env_cfg = config_path(annotate={"ink": "#FF0000"})
    explicit_cfg = config_path(annotate={"ink": "#00FF00"})
    output = tmp_path / "explicit-config.png"

    result = run_script(
        "annotate_micrograph.py",
        env_cfg,
        synthetic_image,
        output,
        "--config",
        explicit_cfg,
        "--point",
        "structure A:400,300",
    )

    assert_success(result)
    image, numpy = image_modules
    with image.open(output) as rendered:
        pixels = numpy.asarray(rendered.convert("RGB"), dtype=numpy.int16)
    target = numpy.array((0, 255, 0), dtype=numpy.int16)
    assert int(numpy.all(numpy.abs(pixels - target) <= 40, axis=2).sum()) >= 50


@pytest.mark.parametrize("script", ["annotate_micrograph.py", "gridref.py"])
def test_empty_font_path_uses_the_bundled_sans_through_ltcore_fonts(
    script, tmp_path, synthetic_image, image_modules, config_path, run_script,
):
    cfg = config_path(annotate={"font_path": ""})
    output = tmp_path / f"{Path(script).stem}-bundled.png"
    arguments = [synthetic_image, output]
    if script == "annotate_micrograph.py":
        arguments += ["--point", "structure A:400,300", "--caption", "alpha"]
    result = run_script(script, cfg, *arguments)
    assert_success(result)
    assert "font: fonts.sans " in result.stderr, result.stderr
    assert ".ttf" in result.stderr.split("font: fonts.sans ", 1)[1].splitlines()[0]
    assert "pillow-default" not in result.stderr


def test_lt_upload_pixels_match_v010(tmp_path, synthetic_image, config_path, run_script):
    import hashlib
    from PIL import Image, ImageFont, features, __version__
    import importlib.util
    spec = importlib.util.spec_from_file_location("annotation_cases", Path(__file__).with_name("annotation_cases.py"))
    cases = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cases)
    CASES = cases.CASES
    fixture = json.loads((Path(__file__).parent/'fixtures/annotate-v010-digests.json').read_text())
    for index, marks in enumerate(CASES):
        outputs = []
        for name, settings, flags in [('copies', {}, []), ('single', {'palette_copies':False}, []), ('lt', {}, ['--palette','lt'])]:
            cfg = config_path(annotate=settings)
            target = tmp_path / f'{name}-{index}.png'
            assert_success(run_script('annotate_micrograph.py',cfg,synthetic_image,target,*marks,*flags))
            outputs.append(target)
        assert outputs[0].read_bytes() == outputs[1].read_bytes() == outputs[2].read_bytes()
        if fixture['pillow'] == __version__ and fixture['freetype'] == features.version('freetype2'):
            face = ImageFont.truetype(str(Path('shared/fonts/noto-sans/NotoSans-Variable.ttf')),22)
            digest = hashlib.sha256(Image.open(outputs[0]).convert('RGB').tobytes()).hexdigest()
            assert digest == fixture['engines'][face.layout_engine.name][index]
    if fixture['pillow'] != __version__ or fixture['freetype'] != features.version('freetype2'):
        pytest.skip('historical digests require Pillow '+fixture['pillow']+' / FreeType '+fixture['freetype']+
                    '; upload byte equality passed for all 3 render modes')


def test_one_copy_per_pdf_palette(tmp_path, synthetic_image, run_script):
    from PIL import Image
    from ltcore import palettes as P
    cfg = tmp_path/'palettes.toml'
    cfg.write_text('[style]\npalettes = ["catppuccin-frappe", "everforest-dark-medium"]\nprint_palette = "catppuccin-latte"\n')
    target = tmp_path/'out.png'
    assert_success(run_script('annotate_micrograph.py',cfg,synthetic_image,target,'--point','alpha:240,220'))
    for pid in ['catppuccin-latte','catppuccin-frappe','everforest-dark-medium']:
        copy = Image.open(tmp_path/f'out-{pid}.png').convert('RGB')
        colours = P.micrograph_colours(pid,print_copy=pid=='catppuccin-latte')
        expected = tuple(bytes.fromhex(colours['margin'][1:]))
        assert copy.getpixel((0,0)) == expected
        import numpy as np
        assert np.any(np.all(np.asarray(copy) == tuple(bytes.fromhex(colours['point'][1:])), axis=-1))
    cfg.write_text(cfg.read_text()+'[annotate]\npalette_copies = false\n')
    result = run_script('annotate_micrograph.py',cfg,synthetic_image,target,'--point','alpha:240,220')
    assert_success(result)
    assert not P.read_copies(target)['copies']
    assert all(f'out-{pid}.png' in result.stderr for pid in ['catppuccin-latte','catppuccin-frappe','everforest-dark-medium'])
    # Disabled renders keep warning after the previous manifest has become empty.
    result = run_script('annotate_micrograph.py',cfg,synthetic_image,target,'--point','alpha:240,220')
    assert_success(result)
    assert all(f'out-{pid}.png' in result.stderr for pid in ['catppuccin-latte','catppuccin-frappe','everforest-dark-medium'])
    cfg.write_text('[style]\npalettes = ["primer-light-colorblind"]\nprint_palette = "primer-light-colorblind"\n')
    result = run_script('annotate_micrograph.py',cfg,synthetic_image,target,'--point','alpha:240,220')
    assert_success(result)
    assert 'WARNING: palette primer-light-colorblind' in result.stderr
    assert not P.read_copies(target)['copies']


def test_key_on_copies_only(tmp_path, synthetic_image, config_path, run_script):
    from PIL import Image
    cfg = config_path()
    cases = [(['--point','alpha:240,220'],False),
             (['--point','alpha:240,220','--ring','round:490,300:65,80','--caption','Short'],False),
             (['--point','alpha:240,220','--ring','round:490,300:65,80','--bracket','span:300,100:300,450','--caption','A caption that fills the entire available caption row '*3],True)]
    for i,(marks,taller) in enumerate(cases):
        target = tmp_path/f'key-{i}.png'
        assert_success(run_script('annotate_micrograph.py',cfg,synthetic_image,target,*marks))
        copy = target.with_name(f'key-{i}-tol-bright.png')
        assert (Image.open(copy).height > Image.open(target).height) == taller


def test_copies_capped(tmp_path, config_path, run_script, image_modules):
    Image,_ = image_modules
    source, target = tmp_path/'large.png', tmp_path/'out.png'
    Image.new('RGB',(3000,600),'white').save(source)
    cfg = config_path()
    for cap in (1920,0):
        assert_success(run_script('annotate_micrograph.py',cfg,source,target,'--point','alpha:700,300','--copy-max',cap))
        upload = Image.open(target)
        copy = Image.open(tmp_path/'out-tol-bright.png')
        assert upload.width > 3000
        assert max(copy.size) == (1920 if cap else max(upload.size))


def test_file_copies(tmp_path, synthetic_image, config_path, run_script):
    from ltcore import palettes as P
    cfg = config_path()
    source, media = tmp_path/'rendered.jpg', tmp_path/'lt-m-u5.jpg'
    assert_success(run_script('annotate_micrograph.py',cfg,synthetic_image,source,'--point','alpha:240,220'))
    media.write_bytes(source.read_bytes())
    before = media.read_bytes()
    assert_success(run_script('annotate_micrograph.py',cfg,source,media,'--file-copies'))
    assert P.read_copies(media)['media_sha256'] == P.file_sha256(media)
    for pid in P.read_copies(source)['copies']:
        assert P.bound_copy(media,pid).is_file()
    # A second filing replaces owned copies; an edited sibling remains untouched.
    assert_success(run_script('annotate_micrograph.py',cfg,source,media,'--file-copies'))
    sibling = tmp_path/'lt-m-u5-tol-bright.jpg'
    sibling.write_bytes(b'unrelated')
    result = run_script('annotate_micrograph.py',cfg,source,media,'--file-copies')
    assert_success(result)
    assert sibling.read_bytes() == b'unrelated' and 'unowned' in result.stderr
    assert P.bound_copy(media,'tol-bright') is None
    assert_success(run_script('annotate_micrograph.py',cfg,source,media,'--file-copies','--overwrite'))
    assert P.bound_copy(media,'tol-bright')
    src_sibling = P.bound_copy(source,'tol-bright')
    src_sibling.write_bytes(b'edited')
    result = run_script('annotate_micrograph.py',cfg,source,media,'--file-copies')
    assert_success(result)
    assert 'does not match' in result.stderr and P.bound_copy(media,'tol-bright') is None
    result = run_script('annotate_micrograph.py',cfg,source,media)
    assert result.returncode != 0 and 'nothing to draw' in result.stderr
    assert media.read_bytes() == before


@pytest.mark.parametrize('pid', ['test--blue', 'test-blue-'])
def test_file_copies_accepts_custom_palette_ids(tmp_path, synthetic_image, run_script, pid):
    from ltcore import palettes as P
    custom = tmp_path / 'custom.toml'
    custom.write_text(f'''[[palette]]
id = "{pid}"
name = "Test blue"
mode = "light"
[palette.roles]
background = "#ffffff"
text = "#111111"
accent_1 = "#004488"
accent_2 = "#ddaa33"
accent_3 = "#bb5566"
accent_4 = "#117733"
accent_5 = "#44aa99"
accent_6 = "#882255"
''')
    cfg = tmp_path / 'config.toml'
    cfg.write_text(f'[style]\npalettes = ["{pid}"]\nprint_palette = "{pid}"\n'
                   f'custom_palettes_file = {json.dumps(str(custom))}\n')
    source, media = tmp_path / 'rendered.png', tmp_path / 'lt-test.png'
    assert_success(run_script('annotate_micrograph.py', cfg, synthetic_image, source,
                              '--point', 'alpha:240,220'))
    assert P.bound_copy(source, pid).is_file()
    media.write_bytes(source.read_bytes())
    result = run_script('annotate_micrograph.py', cfg, source, media, '--file-copies')
    assert_success(result)
    assert P.bound_copy(media, pid) is not None, result.stderr
    assert P.bound_copy(media, pid).read_bytes() == P.bound_copy(source, pid).read_bytes()
    assert 'refused invalid palette id' not in result.stderr


def test_render_owns_its_copies(tmp_path, synthetic_image, config_path, run_script):
    from ltcore import palettes as P
    cfg = config_path()
    target = tmp_path/'out.png'
    arguments = [synthetic_image,target,'--point','alpha:240,220']
    assert_success(run_script('annotate_micrograph.py',cfg,*arguments))
    assert_success(run_script('annotate_micrograph.py',cfg,*arguments))
    sibling = P.bound_copy(target,'tol-bright')
    sibling.write_bytes(b'unrelated')
    result = run_script('annotate_micrograph.py',cfg,*arguments)
    assert_success(result)
    assert 'unowned' in result.stderr and sibling.read_bytes() == b'unrelated'
    assert P.bound_copy(target,'tol-bright') is None
    assert_success(run_script('annotate_micrograph.py',cfg,*arguments,'--overwrite'))
    assert P.bound_copy(target,'tol-bright')


def test_missing_labels_path_warns(tmp_path, synthetic_image, run_script):
    cfg = tmp_path/'labels.toml'
    cfg.write_text('[fonts.labels]\nsans = "missing.ttf"\n')
    result = run_script('annotate_micrograph.py',cfg,synthetic_image,tmp_path/'out.png','--point','alpha:240,220')
    assert_success(result)
    assert 'WARNING: using pillow-default' in result.stderr
    assert 'fonts.labels.sans' in result.stderr and 'does not exist' in result.stderr
