# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The font files behind a build: static instances for rsvg-convert, and @font-face CSS for the page.

cairo's PDF surface embeds a variable font's default instance whenever a run goes through its CID
subset (non-ASCII such as curly quotes, "µ" or "°" sends it there), so bold text can come out
regular. Static instances cannot lose their weight. static_set() cuts one static instance per face
the diagram uses (the configured sans at the text weights, upright and italic; the configured mono,
bold) from any variable file, under a renamed family "<Family>Static", into the lt-suite cache folder
(<cache>/font-cache/), and writes a private fontconfig that rsvg-convert reads. Static files are used
as they are. Instances are cut once per set of source files and reused, so their bytes stay put.

page_css() returns @font-face rules with the font data inline, so the interactive page shows the
diagram in its own faces without a network request (fonts.page_fonts = "local").
"""
import base64
import hashlib
import io
from functools import lru_cache
from xml.sax.saxutils import escape
from pathlib import Path

import core as E
import style


def _is_variable(path):
    from fontTools.ttLib import TTFont
    return "fvar" in TTFont(str(path), lazy=True)


def faces():
    """[(css family, weight, italic, source file)] for every face a build sets text in."""
    E.ensure_fonts()
    out = [(E.FONT, w, it, p) for (w, it), p in sorted(E.FILES.items(), key=lambda kv: (kv[0][1], kv[0][0]))]
    mono = style.font_family("mono")
    out.append((mono, 700, False, style.font_path("mono_bold", 700)))
    return out


def _static_name(family):
    return "".join(ch for ch in family if ch.isalnum()) + "Static"


def _style_name(weight, italic):
    return {500: "Medium", 700: "Bold", 400: "Regular", 600: "SemiBold"}.get(int(weight), str(weight)) + \
        (" Italic" if italic else "")


def _instance_path(dest, family, weight, italic):
    return dest / f"{family}-{_style_name(weight, italic).replace(' ', '')}.ttf"


def _cut(src, dest, family, weight, italic, opsz):
    """A static instance of a variable font at one weight (and optical size), renamed."""
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    f = TTFont(str(src))
    axes = {a.axisTag: a for a in f["fvar"].axes}
    loc = {}
    for tag, a in axes.items():
        if tag == "wght":
            loc[tag] = max(a.minValue, min(a.maxValue, weight))
        elif tag == "opsz":
            loc[tag] = max(a.minValue, min(a.maxValue, opsz or E.FS))
        elif tag == "ital":
            loc[tag] = 1 if italic else 0
        else:
            loc[tag] = a.defaultValue
    f = instantiateVariableFont(f, loc)
    style_name = _style_name(weight, italic)
    ps = f"{family}-{style_name.replace(' ', '')}"
    name = f["name"]
    for nid in (16, 17, 21, 22, 25):
        name.removeNames(nameID=nid)
    for nid, val in ((1, family), (2, style_name), (4, f"{family} {style_name}"), (6, ps)):
        name.setName(val, nid, 3, 1, 0x409)
        name.setName(val, nid, 1, 0, 0)
    f["OS/2"].usWeightClass = int(weight)
    if weight >= 700:
        f["OS/2"].fsSelection = (f["OS/2"].fsSelection | 0x20) & ~0x40
        f["head"].macStyle |= 1
    else:
        f["OS/2"].fsSelection = f["OS/2"].fsSelection & ~0x20
        f["head"].macStyle &= ~1
    if italic:
        f["OS/2"].fsSelection = (f["OS/2"].fsSelection | 0x01) & ~0x40
        f["head"].macStyle |= 2
    out = _instance_path(dest, family, weight, italic)
    tmp = out.with_suffix(".tmp")
    f.save(tmp)
    tmp.replace(out)
    return out


def static_set():
    """(FONTCONFIG_FILE path, {family in the SVG: family rsvg-convert should use}) for the current faces."""
    fs = faces()
    sig = hashlib.sha256()
    for fam, w, it, p in fs:
        st = Path(p).stat()
        sig.update(f"{fam}|{w}|{it}|{Path(p).resolve()}|{st.st_size}|{int(st.st_mtime)}|{E.FS}\n".encode())
    dest = style.cache_dir("font-cache", "static-" + sig.hexdigest()[:12])
    rename, dirs = {}, []
    for fam, w, it, p in fs:
        if _is_variable(p):
            sfam = _static_name(fam)
            if not _instance_path(dest, sfam, w, it).exists():
                _cut(p, dest, sfam, w, it, E.OPSZ)
            rename[fam] = sfam
        else:
            d = str(Path(p).resolve().parent)
            if d not in dirs:
                dirs.append(d)
    conf = dest / "fonts.conf"
    body = "".join(f"  <dir>{escape(d)}</dir>\n" for d in [str(dest)] + dirs)
    text = ('<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n<fontconfig>\n'
            '  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>\n'
            f"{body}  <cachedir>{escape(str(dest / 'cache'))}</cachedir>\n</fontconfig>\n")
    if not conf.exists() or conf.read_text() != text:
        conf.write_text(text)
    return conf, rename


def _has_reserved_name(path):
    from fontTools.ttLib import TTFont
    name = TTFont(str(path), lazy=True)["name"]
    for nid in (0, 13):
        v = name.getDebugName(nid) or ""
        if "reserved font name" in v.lower():
            return True
    return False


def _subset(path, text, weight, italic):
    """The font cut down to the characters on the page (a variable file instanced first), as TrueType bytes:
    the same on every machine (WOFF2 would depend on an optional compressor). A font with a Reserved Font
    Name is never modified: it goes in whole."""
    if _has_reserved_name(path):
        return Path(path).read_bytes(), "truetype"
    from fontTools import subset
    from fontTools.ttLib import TTFont
    f = TTFont(str(path))
    if "fvar" in f:
        from fontTools.varLib.instancer import instantiateVariableFont
        axes = {a.axisTag: a for a in f["fvar"].axes}
        loc = {t: (max(a.minValue, min(a.maxValue, weight)) if t == "wght" else
                   (1 if italic else 0) if t == "ital" else
                   max(a.minValue, min(a.maxValue, E.OPSZ or E.FS)) if t == "opsz" else a.defaultValue)
               for t, a in axes.items()}
        f = instantiateVariableFont(f, loc)
    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.name_legacy = True
    opts.notdef_outline = True
    sub = subset.Subsetter(opts)
    sub.populate(text=text)
    sub.subset(f)
    buf = io.BytesIO()
    f.recalcTimestamp = False
    f.save(buf)
    return buf.getvalue(), "truetype"


def page_css(text):
    """@font-face rules for every face, holding only the characters in `text` (the page's SVG text)."""
    chars = "".join(sorted(set(text) | set(" 0123456789")))
    rules = []
    for fam, w, it, p in faces():
        data, fmt = _subset(p, chars, w, it)
        mime = "font/woff2" if fmt == "woff2" else "font/ttf"
        rules.append(f"@font-face {{ font-family: '{fam}'; font-style: {'italic' if it else 'normal'}; "
                     f"font-weight: {w}; src: url(data:{mime};base64,{base64.b64encode(data).decode()}) "
                     f"format('{fmt}'); }}")
    return "\n".join(rules)


def google_css_url():
    """The Google Fonts stylesheet for the configured faces (fonts.page_fonts = "google")."""
    sans, mono = E.FONT, style.font_family("mono")
    w0, w1 = E.WGHT
    return ("https://fonts.googleapis.com/css2?family=" + sans.replace(" ", "+") +
            f":ital,wght@0,{w0};0,{w1};1,{w0};1,{w1}&family=" + mono.replace(" ", "+") + ":wght@700&display=swap")


@lru_cache(maxsize=32)
def _drawio_face(path, mtime_ns, size, text, weight, italic):
    """Cache embedded static subsets by source identity and displayed characters."""
    data, _ = _subset(path, text, weight, italic)
    identity = Path(path).read_bytes() + f"|{weight}|{italic}|{E.OPSZ or E.FS}".encode()
    alias = "DiagramFace" + hashlib.sha256(identity).hexdigest()[:16]
    return alias, "data:font/ttf;base64," + base64.b64encode(data).decode()


def drawio_fonts(text):
    """Face aliases and draw.io's extFonts attribute, with resolved font data for every weight and style."""
    chars = "".join(sorted(set(text) | set(" 0123456789")))
    aliases, sources = {}, {}
    mono = style.font_family("mono")
    for family, weight, italic, path in faces():
        path = Path(path)
        st = path.stat()
        alias, uri = _drawio_face(str(path), st.st_mtime_ns, st.st_size, chars, weight, italic)
        aliases[(family, weight >= 600, italic)] = alias
        sources[alias] = uri
        if family == mono:
            for bold in (False, True):
                for it in (False, True):
                    aliases[(family, bold, it)] = alias
    return aliases, "|".join(f"{name}^{uri}" for name, uri in sources.items())
