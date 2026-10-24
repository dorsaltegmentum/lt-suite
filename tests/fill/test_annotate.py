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
