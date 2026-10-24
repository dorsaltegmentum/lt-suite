#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validate a capture that sink.py or collect_downloads.py put in <data>/incoming/, then
move it to <data>/captures/ with its media paths filled in.

    python3 stamp_capture.py <module-id | path/to/module-<id>.json> [--media-dir DIR]

Steps, in order; nothing moves unless the module validates:
  1. Pick the capture: a given path, else the newest incoming copy for the id
     (module-<id>.json or module-<id>.<n>.json, highest n), else the existing
     captures/module-<id>.json (a re-stamp after fetching more images).
  2. Drop legacy fields (`permission`) and print one note.
  3. Validate against module.schema.json and the module rails. On failure the files stay in
     incoming/ and the validator's exit code is returned.
  4. Fill `media[].local_path` from captures/media/lt-<id>-<key>.<ext> (student uploads:
     lt-<id>-u<key>.<ext>), counting the images waiting in incoming/media/ as present, and
     validate again. Paths are relative to the capture file. `--media-dir DIR` resolves the
     paths against DIR instead and leaves incoming/media alone; the module is still stamped.
  5. Validate any incoming reference (JSON object, same module_id, reference schema) before
     any file moves; on failure every incoming file stays. Then move
     incoming/media/lt-<id>-* to captures/media/ (an identical file is a no-op; a different
     one is kept in incoming/ and reported), write captures/module-<id>.json, and move older
     incoming copies of the module to incoming/superseded/. Folders are 0700 and files 0600,
     whatever the umask.
  6. Publish the validated reference bytes to captures/. A reference is written once: when
     captures/ already holds a different one, the new one stays in incoming/ and is
     reported.
  7. Print the question digest for the run report. The capture file does not store it: the
     file holds what Lt returned, and a hash saved next to that data would go stale after
     any edit.

Exit codes: 0 when the capture is stamped and valid; 1 when a rail fails; 2 when the schema
fails; 3 for a usage, file or config error, or a missing Python package.
"""
import sys

if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: "
             "python3 <setup skill>/scripts/setup.py bootstrap")
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_shared"))
import ltcore  # noqa: E402

_MISSING = ltcore.bootstrap()
from ltcore import config, store  # noqa: E402

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import stat  # noqa: E402
import tempfile  # noqa: E402
from typing import Callable  # noqa: E402

EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg")
INCOMING_RE = re.compile(r"^module-([A-Za-z0-9_-]{1,64})(?:\.(\d{1,6}))?\.json$")


def backfill_local_paths(doc: dict, capture_path: Path, media_dir: Path,
                         exists: Callable[[Path], bool] | None = None) -> tuple[int, int, int]:
    """Set each media entry's local_path to the file fetch_media.js saved.

    Returns (found, keyed, no_key). capture.js writes `local_path: null`, since a web page has
    no view of the file system. Files are matched by name: lt-<module_id>-<key>.<ext>. Lt
    counts uploads and author images separately, so uploads carry a `u` (lt-<module_id>-u<key>);
    a name without the `u` counts for an upload only if no author image in the module uses
    the same key.
    A `key: null` entry is an empty student-upload slot: nothing exists to fetch, so it is
    counted apart and never reported as a fetch failure. ``exists`` lets the caller count
    files that are about to be moved into ``media_dir``.
    """
    exists = exists or (lambda p: p.is_file())
    mid = doc.get("module_id")
    found = keyed = no_key = 0

    author_keys = set()
    for pg in doc.get("pages", []) or []:
        groups = [pg.get("media")] + [it.get("media") for it in pg.get("items", []) or []]
        groups += [c.get("media") for c in pg.get("content", []) or []]
        author_keys |= {str(e["key"]) for g in groups for e in g or []
                        if e.get("key") and e.get("role") != "student-upload"}

    def stems(e):
        key = str(e["key"])
        if e.get("role") != "student-upload":
            return [key]
        return [f"u{key}"] if key in author_keys else [f"u{key}", key]

    def visit(entries):
        nonlocal found, keyed, no_key
        for e in entries or []:
            if not e.get("key"):
                no_key += 1
                continue
            keyed += 1
            cands = [media_dir / f"lt-{mid}-{s}{ext}" for s in stems(e) for ext in EXTS]
            hit = next((c for c in cands if exists(c)), None)
            if hit is None:
                if e.get("role") == "student-upload":
                    e["local_path"] = None      # an old path without `u` could point at an author image
                continue
            try:
                e["local_path"] = os.path.relpath(hit, capture_path.parent).replace(os.sep, "/")
            except ValueError:                  # another drive on Windows
                e["local_path"] = str(hit)
            found += 1

    # Media sit at pages[].media, pages[].items[].media and pages[].content[].media.
    for pg in doc.get("pages", []) or []:
        visit(pg.get("media"))
        for it in pg.get("items", []) or []:
            visit(it.get("media"))
        for panel in pg.get("content", []) or []:
            visit(panel.get("media"))
    return found, keyed, no_key


def incoming_copies(incoming: Path, module_id: str) -> list[Path]:
    """Incoming copies of one module, oldest first (module-<id>.json, then .1, .2, ...)."""
    out = []
    if incoming.is_dir():
        for p in incoming.iterdir():
            m = INCOMING_RE.match(p.name)
            if m and m.group(1) == module_id and p.is_file():
                out.append((int(m.group(2) or 0), p))
    return [p for _, p in sorted(out)]


def private_dir(path: Path, follow_link: bool = False) -> None:
    """Make ``path`` a folder with mode 0700, whatever the umask.

    Missing folders, ancestors included, are created and set to 0700. An existing folder this
    account owns is set to 0700; one another account owns keeps its mode and gets a WARN line.
    A link in place of a folder is refused (OSError) unless ``follow_link`` is set, which the
    data and captures roots use: a user may keep them elsewhere and link to them.
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


def write_json_atomic(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def publish(src: Path, dst: Path, move: bool) -> None:
    """Copy or move ``src`` to ``dst`` without ever replacing ``dst`` (FileExistsError)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)                # atomic, and refuses an existing name
    except FileExistsError:
        raise
    except OSError:                      # another file system, or no hard links
        with open(src, "rb") as fin, open(dst, "xb") as fout:
            shutil.copyfileobj(fin, fout)
        shutil.copystat(src, dst)
    os.chmod(dst, 0o600)
    if move:
        src.unlink()


def publish_bytes(data: bytes, dst: Path) -> None:
    """Write ``data`` at ``dst`` only if nothing is there (FileExistsError otherwise)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".tmp", dir=dst.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        try:
            os.link(tmp, dst)
        except FileExistsError:
            raise
        except OSError:
            with open(dst, "xb") as fh:
                fh.write(data)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def free_name(folder: Path, name: str) -> Path:
    """``folder/name``, or ``folder/<stem>.<n><suffix>`` with the smallest free n."""
    p = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 0
    while p.exists():
        n += 1
        p = folder / f"{stem}.{n}{suffix}"
    return p


def copy_index(p: Path) -> int:
    m = INCOMING_RE.match(p.name)
    return int(m.group(2) or 0) if m else -1


def same_json(a: Path, b: Path) -> bool:
    try:
        return json.loads(a.read_text(encoding="utf-8")) == json.loads(b.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False


def print_result(res, name: str) -> None:
    if res.exit_code == 3:
        print(f"FATAL: {res.fatal}", file=sys.stderr)
        return
    for n in res.notes:
        print(f"  note: {n}")
    if res.schema_errors:
        print(f"SCHEMA FAIL: {name} against {res.schema_name}: {len(res.schema_errors)} error(s)")
        for e in res.schema_errors:
            print(f"  {e}")
    if res.rail_errors:
        print(f"RAIL FAIL: {name}: {len(res.rail_errors)} violation(s)")
        for e in res.rail_errors:
            print(f"  - {e}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="module id, or the path of a module-<id>.json")
    ap.add_argument("--media-dir", type=Path,
                    help="resolve media paths against this folder instead of captures/media; "
                         "images in incoming/media are not moved")
    ap.add_argument("--config", help="config file (default: the normal lookup)")
    ap.add_argument("--data-dir", help="data folder (overrides LT_SUITE_DATA and the config)")
    ap.add_argument("--keep-incoming", action="store_true",
                    help="copy from incoming/ instead of moving (the incoming files stay)")
    a = ap.parse_args(argv)

    if _MISSING:
        print(ltcore.missing_hint(_MISSING), file=sys.stderr)
        return 3
    from ltcore import digest, validate  # needs jsonschema, checked above

    try:
        cfg = config.load(a.config)
    except config.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    st = store.Store(cfg, data_dir=a.data_dir)

    # 1. pick the capture
    target = Path(a.target).expanduser()
    if target.suffix == ".json" or os.sep in a.target or "/" in a.target:
        if not target.is_file():
            print(f"error: {target} does not exist", file=sys.stderr)
            return 3
        src = target.resolve()
        m = INCOMING_RE.match(src.name)
        if not m:
            print(f"error: {src.name} is not named module-<id>.json", file=sys.stderr)
            return 3
        module_id = m.group(1)
    else:
        try:
            module_id = store.check_id(a.target)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 3
        copies = incoming_copies(st.incoming_dir, module_id)
        if copies:
            src = copies[-1]
        elif st.capture_path(module_id).is_file():
            src = st.capture_path(module_id)
        else:
            print(f"error: no module-{module_id}.json in {st.incoming_dir} or {st.captures_dir}",
                  file=sys.stderr)
            return 3
    try:
        doc = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"error: {src}: {e}", file=sys.stderr)
        return 3
    if not isinstance(doc, dict) or doc.get("module_id") != module_id:
        print(f"error: {src.name} holds module_id {doc.get('module_id') if isinstance(doc, dict) else None!r}, "
              f"not {module_id!r}", file=sys.stderr)
        return 3
    print(f"capture: {src}")

    # 2. legacy fields
    dropped = validate.drop_legacy_fields(doc)
    if dropped:
        print(f"note: {validate.legacy_note(dropped)}")

    # 3. validate the document as it arrived, so a malformed one never reaches the walk below
    dest = st.capture_path(module_id)
    res = validate.validate_doc(doc, "module", base_dir=st.captures_dir)
    if res.exit_code != 0:
        print_result(res, dest.name)
        print(f"REFUSED: {src.name} failed validation and stays where it is.", file=sys.stderr)
        return res.exit_code

    # 4. media paths, then validate again with them filled in
    incoming_media = st.incoming_dir / "media"
    pending: dict[str, Path] = {}
    if a.media_dir is None and incoming_media.is_dir():
        pending = {p.name: p for p in incoming_media.glob(f"lt-{module_id}-*") if p.is_file()}
    media_dir = a.media_dir.expanduser().resolve() if a.media_dir else st.media_dir

    def exists(p: Path) -> bool:
        return p.is_file() or (p.parent == media_dir and p.name in pending)

    found, keyed, no_key = backfill_local_paths(doc, dest, media_dir, exists)
    if keyed or no_key:
        print(f"local_path: {found} of {keyed} keyed media entries resolved against {media_dir}")
        if found < keyed:
            print(f"  {keyed - found} keyed entries have no file: fetch_media.js has not run on "
                  f"their page, or the image was blocked cross-origin. Name them in the run report.")
        if no_key:
            print(f"  {no_key} entries carry key null: empty student-upload slots, nothing to fetch.")

    res = validate.validate_doc(doc, "module", base_dir=st.captures_dir)
    print_result(res, dest.name)
    if res.exit_code != 0:
        print(f"REFUSED: {src.name} failed validation and stays where it is.", file=sys.stderr)
        return res.exit_code

    # Validate reference bytes before publishing any module or moving any media.
    ref_in = st.incoming_dir / f"reference-{module_id}.json"
    ref_out = st.reference_path(module_id)
    reference_raw = None
    if ref_in.is_file():
        try:
            reference_raw = ref_in.read_bytes()
            ref_doc = json.loads(reference_raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as e:
            print(f"REFUSED reference: {ref_in.name} is not JSON ({e.__class__.__name__}); "
                  "incoming files stay untouched.", file=sys.stderr)
            return 2
        if not isinstance(ref_doc, dict) or ref_doc.get("module_id") != module_id:
            print(f"REFUSED reference: {ref_in.name} does not name module {module_id}; "
                  "incoming files stay untouched.", file=sys.stderr)
            return 2
        rres = validate.validate_doc(ref_doc, "reference", base_dir=st.captures_dir,
                                     config_path=a.config)
        print_result(rres, ref_in.name)
        if rres.exit_code != 0:
            print("REFUSED reference: validation failed; incoming files stay untouched.", file=sys.stderr)
            return rres.exit_code

    # 5. media, module, superseded copies, in 0700 folders whatever the umask
    try:
        private_dir(st.data_dir, follow_link=True)
        private_dir(st.captures_dir, follow_link=True)
        if pending:
            private_dir(st.media_dir)
    except OSError as e:
        print(f"error: {e}; incoming files stay untouched.", file=sys.stderr)
        return 3
    moved = kept = 0
    if pending:
        for name, p in sorted(pending.items()):
            target_media = st.media_dir / name
            if target_media.exists():
                if target_media.read_bytes() == p.read_bytes():
                    if not a.keep_incoming:
                        p.unlink()
                    continue
                kept += 1
                print(f"KEPT in incoming: {name} differs from captures/media/{name}; "
                      f"compare the two and replace by hand if the new image is wanted")
                continue
            try:
                publish(p, target_media, move=not a.keep_incoming)
            except FileExistsError:
                kept += 1
                print(f"KEPT in incoming: {name} appeared in captures/media while stamping")
                continue
            moved += 1
        print(f"media: {moved} image(s) moved to {st.media_dir}" + (f", {kept} kept in incoming" if kept else ""))

    write_json_atomic(dest, doc)
    in_incoming = src.resolve().parent == st.incoming_dir.resolve()
    if src.resolve() != dest.resolve() and in_incoming and not a.keep_incoming:
        src.unlink()
    # Only copies older than the one stamped are set aside; a newer one waits for its own stamp.
    cut = copy_index(src) if in_incoming else -1
    older = [p for p in incoming_copies(st.incoming_dir, module_id)
             if p.resolve() != src.resolve() and copy_index(p) < cut]
    if older and not a.keep_incoming:
        sup = st.incoming_dir / "superseded"
        private_dir(sup)
        for p in older:
            shutil.move(str(p), str(free_name(sup, p.name)))
        print(f"moved {len(older)} older incoming cop{'y' if len(older) == 1 else 'ies'} to {sup}")
    print(f"stamped {dest}")

    # 6. reference, written once
    status = 0
    ref_in = st.incoming_dir / f"reference-{module_id}.json"
    ref_out = st.reference_path(module_id)
    if ref_in.is_file():
        if ref_out.exists():
            if same_json(ref_in, ref_out):
                if not a.keep_incoming:
                    ref_in.unlink()
                print(f"reference: {ref_out.name} already in captures, identical")
            else:
                print(f"REFUSED reference: {ref_out} exists and a reference is written once. "
                      f"The new one stays at {ref_in}; delete it, or move the old one aside by hand "
                      f"if a fresh reference is intended.")
        else:
            # Publish exactly the bytes validated before any file was moved.
            try:
                publish_bytes(reference_raw, ref_out)
            except FileExistsError:
                print(f"REFUSED reference: {ref_out} appeared while stamping; it is written once.")
            else:
                if not a.keep_incoming:
                    ref_in.unlink()
                print(f"reference: moved to {ref_out}")

    # 7. digest for the run report
    print(f"question digest: {digest.question_digest(doc)}\n"
          f"  -> store it in answers.json as source_module.question_digest. Prompts, types, "
          f"options and authored constraints feed it; answers do not, so a draft stays valid "
          f"across a re-capture after a fill.")
    print(f"OK: {dest.name} passed the module schema and every rail")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
