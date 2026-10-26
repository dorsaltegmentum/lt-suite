#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Collect capture files from the browser's download folder into <data>/incoming/.

The explicit download transport (`--arg transport=download`) saves module-<id>.json, reference-<id>.json and
lt-<id>-<key>.<ext> images into the browser's download folder. This moves them where
sink.py would have written them, so stamp_capture.py treats both routes alike.

    python3 collect_downloads.py <module-id> [<module-id> ...] [--downloads-dir DIR]
                                 [--mode move|copy] [--since MINUTES] [--dry-run]

Rules:
  * Folder: --downloads-dir, else config paths.downloads_dir, else the platform Downloads
    folder. Mode: --mode, else config paths.downloads_mode (copy when a sandbox cannot
    delete outside its workspace).
  * Chromium browsers append " (1)", " (2)" instead of overwriting, so for each file the
    NEWEST variant by modification time is taken; older variants are listed and left alone.
  * module-<id>.json must parse and name module_id <id>. An existing incoming copy is kept
    and the new one is written as module-<id>.<n>.json, as sink.py does.
  * reference-<id>.json is written once: it is refused when incoming/ or captures/ already
    holds one, and the download stays where it is.
  * Images go to incoming/media/ under their name without the " (n)" suffix; an identical
    existing file is a no-op, a different one is refused.
  * Nothing is ever overwritten.
  * Data folders are 0700 and collected files 0600, whatever the umask. A link may stand in
    for the data folder itself; a link in place of incoming/ or incoming/media/ is refused.

Exit codes: 0 every named module collected or already stamped; 1 a named module had no file in the folder;
2 usage error; 3 config error, the folder is missing, or a data folder is unsafe.
"""
import sys

if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: "
             "python3 <setup skill>/scripts/setup.py bootstrap")
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_shared"))
import ltcore  # noqa: E402

ltcore.bootstrap([])  # standard library only
from ltcore import config, store  # noqa: E402

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import stat  # noqa: E402
import time  # noqa: E402
import tempfile  # noqa: E402

SUFFIX = r"(?: \((\d{1,4})\))?"
MEDIA_EXT = r"(?P<ext>png|jpg|jpeg|gif|webp|bmp)"
# extension -> byte signatures that must start the file, as sink.py checks them
IMAGE_MAGIC = {"png": [b"\x89PNG\r\n\x1a\n"], "jpg": [b"\xff\xd8\xff"], "jpeg": [b"\xff\xd8\xff"],
               "gif": [b"GIF87a", b"GIF89a"], "webp": [b"RIFF"], "bmp": [b"BM"]}


def image_ok(path: Path) -> bool:
    ext = path.suffix.lower().lstrip(".")
    head = path.read_bytes()[:16]
    if not any(head.startswith(s) for s in IMAGE_MAGIC.get(ext, [])):
        return False
    return ext != "webp" or head[8:12] == b"WEBP"


def platform_downloads(environ=None, home: Path | None = None) -> Path:
    """The user's Downloads folder: XDG user-dirs on Linux, ~/Downloads elsewhere."""
    env = os.environ if environ is None else environ
    home = home or Path.home()
    if sys.platform.startswith("linux"):
        cfg_home = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
        dirs = cfg_home / "user-dirs.dirs"
        try:
            for line in dirs.read_text(encoding="utf-8").splitlines():
                m = re.match(r'^\s*XDG_DOWNLOAD_DIR\s*=\s*"(.*)"\s*$', line)
                if m:
                    v = m.group(1).replace("$HOME", str(home))
                    return Path(v)
        except OSError:
            pass
    if os.name == "nt" and env.get("USERPROFILE"):
        return Path(env["USERPROFILE"]) / "Downloads"
    return home / "Downloads"


def variants(folder: Path, pattern: re.Pattern) -> dict[str, list[Path]]:
    """Files whose names match ``pattern``, grouped by the name without the " (n)" suffix."""
    out: dict[str, list[Path]] = {}
    for p in folder.iterdir():
        if not p.is_file() or p.is_symlink():
            continue
        m = pattern.match(p.name)
        if m:
            ext = m.groupdict().get("ext")
            key = m.group("base") + (f".{ext.lower()}" if ext else "")
            out.setdefault(key, []).append(p)
    for v in out.values():
        v.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def numbered_free(folder: Path, stem: str, ext: str) -> Path:
    p = folder / f"{stem}{ext}"
    n = 0
    while p.exists():
        n += 1
        p = folder / f"{stem}.{n}{ext}"
    return p


def private_dir(path: Path, follow_link: bool = False) -> None:
    """Make ``path`` a folder with mode 0700, whatever the umask.

    Missing folders, ancestors included, are created and set to 0700. An existing folder this
    account owns is set to 0700; one another account owns keeps its mode and gets a WARN line.
    A link in place of a folder is refused (OSError) unless ``follow_link`` is set, which the
    data root uses: a user may keep the data folder elsewhere and link to it.
    """
    missing = []
    p = path
    while not p.exists() and not p.is_symlink():
        missing.append(p)
        p = p.parent
    for d in reversed(missing):
        d.mkdir(mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
    if path.is_symlink() and not follow_link:
        raise OSError(f"unsafe data folder: {path} is a link")
    if not path.is_dir():
        raise OSError(f"unsafe data folder: {path} is not a folder")
    st = path.stat()
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        print(f"WARN: {path} belongs to another account; its mode stays {stat.S_IMODE(st.st_mode):o}",
              file=sys.stderr)
        return
    if stat.S_IMODE(st.st_mode) != 0o700:
        os.chmod(path, 0o700)


def transfer(src: Path, dst: Path, mode: str, dry: bool) -> None:
    """Publish an independent 0600 copy, refusing replacement even during a race."""
    if dry:
        return
    private_dir(dst.parent)
    fd, tmp = tempfile.mkstemp(prefix=".collect-", dir=dst.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fout, open(src, "rb") as fin:
            shutil.copyfileobj(fin, fout)
        try:
            os.link(tmp, dst)
        except FileExistsError:
            raise
        except OSError:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            out = os.open(dst, flags, 0o600)
            with os.fdopen(out, "wb") as fout, open(tmp, "rb") as fin:
                os.fchmod(fout.fileno(), 0o600)
                shutil.copyfileobj(fin, fout)
    finally:
        os.unlink(tmp)
    if mode == "move":
        src.unlink()


def doc_ok(path: Path, module_id: str) -> str | None:
    """None when the file is a JSON object naming ``module_id``, else the reason."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as e:
        return f"not JSON ({e.__class__.__name__})"
    if not isinstance(doc, dict) or doc.get("module_id") != module_id:
        return f"module_id is not {module_id}"
    return None


def collect(module_id: str, downloads: Path, st: "store.Store", mode: str, since: float | None,
            dry: bool, log: list[str]) -> bool:
    """Collect one module's files. True when a module was collected or an existing stamped module is available."""
    fresh = (lambda p: True) if since is None else (lambda p: p.stat().st_mtime >= since)
    esc = re.escape(module_id)
    got_module = False
    if not dry:
        private_dir(st.data_dir, follow_link=True)

    def stale_note(base: str, rest: list[Path]) -> None:
        if rest:
            log.append(f"  older copies of {base} left in place: " + ", ".join(p.name for p in rest))

    # module
    mods = variants(downloads, re.compile(rf"^(?P<base>module-{esc}){SUFFIX}\.json$"))
    cands = [p for p in mods.get(f"module-{module_id}", []) if fresh(p)]
    if cands:
        src = cands[0]
        why = doc_ok(src, module_id)
        if why:
            log.append(f"REFUSED {src.name}: {why}")
        else:
            dst = numbered_free(st.incoming_dir, f"module-{module_id}", ".json")
            transfer(src, dst, mode, dry)
            log.append(f"{'would ' if dry else ''}{mode} {src} -> {dst}")
            got_module = True
        stale_note(f"module-{module_id}.json", cands[1:])

    # reference, written once
    refs = variants(downloads, re.compile(rf"^(?P<base>reference-{esc}){SUFFIX}\.json$"))
    rc = [p for p in refs.get(f"reference-{module_id}", []) if fresh(p)]
    if rc:
        src = rc[0]
        name = f"reference-{module_id}.json"
        why = doc_ok(src, module_id)
        if why:
            log.append(f"REFUSED {src.name}: {why}")
        elif (st.captures_dir / name).exists() or (st.incoming_dir / name).exists():
            log.append(f"REFUSED {src.name}: a reference is written once and {name} already exists "
                       f"in captures/ or incoming/; the download stays where it is")
        else:
            dst = st.incoming_dir / name
            transfer(src, dst, mode, dry)
            log.append(f"{'would ' if dry else ''}{mode} {src} -> {dst}")
        stale_note(name, rc[1:])

    # images
    imgs = variants(downloads, re.compile(rf"^(?P<base>lt-{esc}-[A-Za-z0-9_-]{{1,160}}){SUFFIX}\.{MEDIA_EXT}$", re.I))
    n_media = 0
    for key, files in sorted(imgs.items()):
        files = [p for p in files if fresh(p)]
        if not files:
            continue
        src = files[0]
        if not image_ok(src):
            log.append(f"REFUSED {src.name}: its leading bytes do not match the extension")
            continue
        dst = st.incoming_dir / "media" / key
        if dst.exists():
            if dst.read_bytes() == src.read_bytes():
                log.append(f"  {src.name}: identical copy already in incoming/media")
            else:
                log.append(f"REFUSED {src.name}: incoming/media/{dst.name} exists with different bytes; "
                           f"run stamp_capture.py first")
            continue
        transfer(src, dst, mode, dry)
        n_media += 1
        stale_note(dst.name, files[1:])
    if n_media:
        log.append(f"{'would ' if dry else ''}{mode} {n_media} image(s) -> {st.incoming_dir / 'media'}")
    from stamp_capture import backfill_local_paths, incoming_copies
    captures = incoming_copies(st.incoming_dir, module_id)
    capture = cands[0] if dry and cands and got_module else captures[-1] if captures else st.capture_path(module_id)
    if capture.is_file() and doc_ok(capture, module_id) is None:
        doc = json.loads(capture.read_text(encoding="utf-8"))
        missing = []
        def exists(path):
            return path.is_file() or (st.incoming_dir / "media" / path.name).is_file() or (
                dry and any(fresh(p) and image_ok(p) for p in imgs.get(path.name, [])))
        backfill_local_paths(doc, st.capture_path(module_id), st.media_dir, exists, missing)
        log.extend(dict.fromkeys(missing))
    return got_module or (not cands and st.capture_path(module_id).is_file()
                          and doc_ok(st.capture_path(module_id), module_id) is None)


def main(argv: list[str] | None = None) -> int:
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("module_ids", nargs="+", metavar="module-id", help="module id(s), e.g. c1-r101")
    ap.add_argument("--downloads-dir", help="browser download folder (default: config paths.downloads_dir, "
                                            "else the platform Downloads folder)")
    ap.add_argument("--mode", choices=("move", "copy"), help="default: config paths.downloads_mode")
    ap.add_argument("--since", type=float, metavar="MINUTES",
                    help="only files modified in the last MINUTES minutes")
    ap.add_argument("--dry-run", action="store_true", help="print what would happen, change nothing")
    ap.add_argument("--config", help="config file (default: the normal lookup)")
    ap.add_argument("--data-dir", help="data folder (overrides LT_SUITE_DATA and the config)")
    a = ap.parse_args(argv)

    try:
        cfg = config.load(a.config)
    except config.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    ids = []
    for mid in a.module_ids:
        try:
            ids.append(store.check_id(mid))
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    st = store.Store(cfg, data_dir=a.data_dir)
    if a.downloads_dir:
        downloads, where = Path(a.downloads_dir).expanduser(), "flag --downloads-dir"
    elif cfg.get("paths.downloads_dir", ""):
        downloads, where = Path(cfg.get("paths.downloads_dir")), f"config paths.downloads_dir ({cfg.where('paths.downloads_dir')})"
    else:
        downloads, where = platform_downloads(), "default"
    if not downloads.is_dir():
        print(f"error: download folder {downloads} [{where}] does not exist", file=sys.stderr)
        return 3
    mode = a.mode or cfg.get("paths.downloads_mode", "move")
    if mode not in ("move", "copy"):
        print(f"error: paths.downloads_mode must be move or copy, not {mode!r}", file=sys.stderr)
        return 3
    since = time.time() - a.since * 60 if a.since is not None else None

    print(f"downloads {downloads} [{where}] -> {st.incoming_dir} ({mode})")
    status = 0
    for mid in ids:
        log: list[str] = []
        try:
            ok = collect(mid, downloads, st, mode, since, a.dry_run, log)
        except FileExistsError as e:
            log.append(f"REFUSED: {e} appeared while collecting; nothing was overwritten")
            ok = False
        except OSError as e:
            for line in log:
                print(line)
            print(f"error: {e}", file=sys.stderr)
            return 3
        for line in log:
            print(line)
        if not ok:
            print(f"{mid}: no module-{mid}.json collected from {downloads}. The site may need "
                  f"permission for automatic downloads; check the browser's download list.")
            status = 1
        else:
            print(f"{mid}: next, python3 stamp_capture.py {mid}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
