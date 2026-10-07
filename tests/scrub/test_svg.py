"""Phase 6 M4 -- SVG at F1: every editor's trace and every embedded picture's
metadata gone, and the drawing renders exactly as before.

The renderer is `rsvg-convert` (librsvg), independent of the scrubber; the scrubber
itself enforces, on every scrub, that the parsed drawing is unchanged except for what
it removes. The survey's LibreOffice export -- whose embedded photo carried GPS that
ExifTool did not report and MAT2 emptied -- runs where it exists.
"""
from __future__ import annotations

import ast
import base64
import gzip
import os
import re
import shutil
import subprocess
import sys

import pytest
from PIL import Image, ImageChops

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub import cli  # noqa: E402
from src.scrub.dispatch import default_dispatcher  # noqa: E402
from src.scrub.errors import ParseError  # noqa: E402
from src.scrub.formats.svg import f1  # noqa: E402
from src.scrub.formats.svg.handler import SvgHandler  # noqa: E402
from tests.scrub import svg_corpus as sc  # noqa: E402

HAVE_RSVG = shutil.which("rsvg-convert") is not None


def _render(data: bytes, tmp_path, name: str) -> Image.Image:
    src, png = tmp_path / f"{name}.svg", tmp_path / f"{name}.png"
    src.write_bytes(data)
    subprocess.run(["rsvg-convert", "-w", "160", str(src), "-o", str(png)], check=True,
                   capture_output=True)
    return Image.open(png).convert("RGBA")


def _embedded(data: bytes) -> list[bytes]:
    out = []
    for m in re.finditer(rb"data:image/[a-z+.-]+;base64,([A-Za-z0-9+/=\s]+)", data):
        raw = base64.b64decode(re.sub(rb"\s", b"", m.group(1)))
        out.append(raw)
        if raw.lstrip().startswith(b"<svg"):
            out += _embedded(raw)
    return out


def test_the_corpus_imports_nothing_from_the_scrubber():
    tree = ast.parse(open(os.path.join(os.path.dirname(__file__), "svg_corpus.py"),
                          encoding="utf-8").read())
    imported = [n.module for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module]
    assert not [m for m in imported if m.split(".")[0] in ("src", "scrub")]


@pytest.mark.parametrize("shape", sc.SHAPES)
def test_the_planted_values_are_there_before(shape):
    data = sc.build(shape=shape)
    for value in sc.planted(shape=shape):
        assert value in data
    inside = b"".join(_embedded(data))
    for value in sc.embedded_planted():
        assert value in inside


@pytest.mark.parametrize("shape", sc.SHAPES)
def test_every_planted_value_is_gone_inside_and_out(shape):
    out = f1.scrub(sc.build(shape=shape))
    for value in sc.planted(shape=shape):
        assert value not in out, f"{value!r} survived"
    inside = b"".join(_embedded(out))
    for value in sc.embedded_planted():
        assert value not in inside, f"{value!r} survived inside a picture"
    assert f1.residuals(out) == []


@pytest.mark.skipif(not HAVE_RSVG, reason="rsvg-convert not installed")
@pytest.mark.parametrize("shape", sc.SHAPES)
def test_the_drawing_renders_exactly_as_before(shape, tmp_path):
    data = sc.build(shape=shape)
    a = _render(data, tmp_path, "a")
    b = _render(f1.scrub(data), tmp_path, "b")
    assert a.size == b.size and ImageChops.difference(a, b).getbbox() is None


def test_what_the_reader_sees_and_the_renderer_reads_stays():
    lo = f1.scrub(sc.build(shape="libreoffice"))
    assert b'ooo:name="page1"' in lo, "LibreOffice's presentation script reads ooo:"
    sk = f1.scrub(sc.build(shape="sketch"))
    assert b"<title>Artboard</title>" in sk and b"Created with Sketch" not in sk
    ink = f1.scrub(sc.build())
    assert b"Visible label" in ink and b"<?xml-stylesheet" in ink
    assert b"sentinel-editor-state" not in ink


def test_illustrators_entities_naming_adobe_go_and_the_rest_stay():
    out = f1.scrub(sc.build(shape="illustrator"))
    assert b"ns.adobe.com" not in out
    assert b'<!ENTITY ns_svg "http://www.w3.org/2000/svg">' in out
    assert b"adobe_illustrator_pgf" not in out


def test_an_svgz_loses_its_wrappers_name_and_time():
    out = f1.scrub(sc.svgz())
    assert out[3] & 0x08 == 0, "FNAME flag still set"
    assert out[4:8] == bytes(4), "the gzip time survived"
    assert sc.secret("GZNAME").encode() not in out
    assert gzip.decompress(out) == f1.scrub(sc.build())


@pytest.mark.parametrize("shape", sc.SHAPES)
def test_two_documents_differing_only_in_metadata_come_out_identical(shape):
    assert f1.scrub(sc.build(1, shape=shape)) == f1.scrub(sc.build(2, shape=shape))


@pytest.mark.parametrize("shape", sc.SHAPES)
def test_scrubbing_twice_changes_nothing(shape):
    once = f1.scrub(sc.build(shape=shape))
    assert f1.scrub(once) == once


@pytest.mark.parametrize("doc,word", [
    (b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
     b'<svg xmlns="http://www.w3.org/2000/svg">&x;</svg>', "entity"),
    (b'<!DOCTYPE svg [<!ENTITY % p "x">]><svg xmlns="http://www.w3.org/2000/svg"/>',
     "entity"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><g></svg>', "well-formed"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:application/pdf;'
     b'base64,JVBERi0="/></svg>', "not a picture"),
    ("﻿<svg xmlns='http://www.w3.org/2000/svg'/>".encode("utf-16"), "UTF-16"),
])
def test_what_cannot_be_cleaned_safely_is_refused(doc, word):
    with pytest.raises(ParseError, match=word):
        f1.scrub(doc)


def test_a_reference_to_a_local_file_is_kept_and_reported():
    doc = (b'<svg xmlns="http://www.w3.org/2000/svg" '
           b'xmlns:xlink="http://www.w3.org/1999/xlink"><image '
           b'xlink:href="file:///home/sentinel/Pictures/a.png"/></svg>')
    assert b"/home/sentinel/" in f1.scrub(doc)
    assert any("refers to files" in a for a in f1.advise(doc))


@pytest.mark.parametrize("doc", [
    b"<!DOCTYPE html><html><body><svg></svg></body></html>",
    b'<?xml version="1.0"?><rss version="2.0"><channel/></rss>',
    gzip.compress(b"plain text, not a drawing"),
])
def test_other_markup_and_other_gzip_files_are_not_claimed(doc):
    assert not SvgHandler().claims(doc)


def test_the_dispatcher_and_the_cli(tmp_path):
    data = sc.build()
    assert isinstance(default_dispatcher().resolve(data), SvgHandler)
    src, out = tmp_path / "a.svg", tmp_path / "b.svg"
    src.write_bytes(data)
    cli.scrub_file(str(src), str(out), "F1")
    assert out.read_bytes() == f1.scrub(data)


SURVEY = os.path.expanduser(os.environ.get("SVG_SAMPLES",
                                           "~/metadata-research/step2/p6/svg"))


@pytest.mark.skipif(not HAVE_RSVG, reason="rsvg-convert not installed")
@pytest.mark.parametrize("name", ["lo_photo.svg"] + [f"apps/s{i}.svg" for i in range(14)])
def test_the_survey_svgs_scrub_clean_and_render_the_same(name, tmp_path):
    path = os.path.join(SURVEY, name)
    if not os.path.exists(path):
        pytest.skip(f"{name}: survey file not on this machine (p6 plan §0)")
    data = open(path, "rb").read()
    out = f1.scrub(data)
    assert f1.residuals(out) == []
    assert b"SENTINEL" not in b"".join(_embedded(out))
    a, b = _render(data, tmp_path, "a"), _render(out, tmp_path, "b")
    assert a.size == b.size and ImageChops.difference(a, b).getbbox() is None
