"""ZIP at F1 (Phase 6 M5): the archive rebuilt, every member scrubbed by its own
handler, sidecars dropped, unknown members refused unless asked."""
from __future__ import annotations

import io
import os
import struct
import subprocess
import sys
import zipfile
import zlib

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub import cli, resources  # noqa: E402
from src.scrub.dispatch import default_dispatcher  # noqa: E402
from src.scrub.errors import (  # noqa: E402
    ParseError,
    ResourceError,
    UnsupportedFormatError,
)
from src.scrub.formats.ooxml import zipread, zipwrite  # noqa: E402
from src.scrub.formats.zip import appledouble, f1  # noqa: E402
from tests.scrub import zip_corpus as zc  # noqa: E402

Entry = zc.Entry


def _names(data: bytes) -> list[str]:
    return [i.filename for i in zipfile.ZipFile(io.BytesIO(data)).infolist()]


def _member(data: bytes, name: str) -> bytes:
    return zipfile.ZipFile(io.BytesIO(data)).read(name)


# --- every locus ----------------------------------------------------------------

@pytest.mark.parametrize("shape", zc.SHAPES)
def test_every_planted_value_is_gone_at_every_depth(shape):
    data = zc.build(3, shape=shape)
    planted = zc.planted(3, shape)
    before = zc.expanded(data)
    assert all(any(p in b for b in before) for p in planted), "corpus lost a locus"
    out = f1.scrub(data)
    after = zc.expanded(out)
    assert [p for p in planted if any(p in b for b in after)] == []
    assert f1.residuals(out) == []
    assert zipfile.ZipFile(io.BytesIO(out)).testzip() is None


@pytest.mark.parametrize("shape", zc.SHAPES)
def test_variants_differing_only_in_metadata_scrub_to_the_same_bytes(shape):
    outs = {f1.scrub(zc.build(v, shape=shape)) for v in (1, 2, 3)}
    assert len(outs) == 1


def test_unix_producers_scrub_to_the_same_bytes():
    """The container says nothing about who wrote it: Info-ZIP, Finder and Python
    shapes of one content give one archive. (Windows differs in content: FAT has
    no executable bit, so the script is not executable there to begin with.)"""
    outs = {s: f1.scrub(zc.build(1, shape=s)) for s in ("infozip", "ditto", "python")}
    assert len(set(outs.values())) == 1


def test_the_rebuilt_container_holds_only_canonical_values():
    out = f1.scrub(zc.build(1, shape="infozip"))
    a = zipread.read(out)
    assert a.comment == b""
    for e in a.entries:
        assert (e.dos_date, e.dos_time) == f1.CANONICAL_TIME
        assert e.extra_cen == e.extra_loc == e.comment == b""
        assert (e.create_system, e.version_made_by, e.internal_attr) == (3, 20, 0)
        assert e.external_attr in f1.CANONICAL_ATTRS
    assert [e.name for e in a.entries] == sorted(e.name for e in a.entries)


# --- content --------------------------------------------------------------------

def test_content_is_what_it_was():
    data = zc.build(2, shape="infozip")
    out = f1.scrub(data)
    for name, body in zc.content().items():
        assert _member(out, name) == body
    from PIL import Image
    for name in ("photos/photo.jpg", "photos/icon.png"):
        a = Image.open(io.BytesIO(_member(data, name))).convert("RGB").tobytes()
        b = Image.open(io.BytesIO(_member(out, name))).convert("RGB").tobytes()
        assert a == b, name
    inner = _member(out, "photos/nested.zip")
    assert _member(inner, "inner/readme.txt") == zc.text(100)


def test_modes_folders_and_empty_files():
    out = f1.scrub(zc.build(1, shape="ditto"))
    info = {i.filename: i for i in zipfile.ZipFile(io.BytesIO(out)).infolist()}
    assert info["tools/run.sh"].external_attr >> 16 == 0o100755
    assert info["docs/notes.txt"].external_attr >> 16 == 0o100644
    assert info["empty/"].external_attr == (0o40755 << 16) | 0x10
    assert info["docs/empty.txt"].file_size == 0
    assert info["docs/empty.txt"].compress_type == zipfile.ZIP_STORED
    # A folder its files already imply gets no entry of its own.
    assert "docs/" not in info and "photos/" not in info


def test_the_umask_is_gone():
    outs = {f1.scrub(zc.build(v, shape="python")) for v in (1, 2)}   # 0600, 0664
    assert len(outs) == 1


def test_scrub_is_idempotent_and_deterministic():
    data = zc.build(1, shape="windows")
    out = f1.scrub(data)
    assert f1.scrub(out) == out
    assert f1.scrub(data) == out


def test_a_docx_member_is_scrubbed_by_the_docx_handler(tmp_path):
    from tests.scrub import docx_corpus as dc
    docx = open(dc.synthetic(str(tmp_path / "a.docx")), "rb").read()
    data = zc.write([Entry(b"report.docx", docx)])
    from src.scrub.formats.docx import f1 as docx_f1
    assert _member(f1.scrub(data), "report.docx") == docx_f1.scrub(docx)


# --- sidecars -------------------------------------------------------------------

def test_sidecars_are_dropped_and_described():
    data = zc.build(1, shape="ditto")
    out = f1.scrub(data)
    assert not [n for n in _names(out) if "__MACOSX" in n or "/._" in n]
    d = f1.describe(data)
    key = "__MACOSX/photos/._photo.jpg: com.apple.metadata:kMDItemWhereFroms"
    assert zc.secret("HOST", 1) in d[key]
    assert zc.secret("QUARANTINE", 1) in d["__MACOSX/photos/._photo.jpg: "
                                         "com.apple.quarantine"]


def test_ds_store_and_thumbs_db_are_dropped():
    assert "photos/.DS_Store" not in _names(f1.scrub(zc.build(1, shape="infozip")))
    assert "photos/Thumbs.db" not in _names(f1.scrub(zc.build(1, shape="windows")))


def test_a_sidecar_is_decided_by_its_bytes_not_its_name():
    """`._notes` that is not AppleDouble is a file someone named so: kept."""
    data = zc.write([Entry(b"._notes", b"plain text\n"),
                     Entry(b".DS_Store", b"not a Finder file\n")])
    assert _names(f1.scrub(data)) == [".DS_Store", "._notes"]


def test_a_resource_fork_dropped_with_its_sidecar_is_named():
    """Limit #60: the report names a file whose resource fork goes."""
    side = zc.appledouble({"com.apple.provenance": bytes(11)}, fork=b"FORK" * 64)
    data = zc.write([Entry(b"font.suit", b"x\n"), Entry(b"__MACOSX/._font.suit", side)])
    assert f1.describe(data)["__MACOSX/._font.suit: resource fork"] == "(256 bytes)"
    assert _names(f1.scrub(data)) == ["font.suit"]


def test_appledouble_reader_survives_garbage():
    assert appledouble.attributes(b"\x00\x05\x16\x07" + b"\xff" * 40) == {}
    assert appledouble.attributes(b"nope") == {}


# --- what is refused, and what is kept on request -------------------------------

def test_an_unknown_member_is_refused_by_name():
    data = zc.write([Entry(b"a/blob.bin", bytes(range(256))),
                     Entry(b"b/other.dat", b"\x00\x01binary"),
                     Entry(b"notes.txt", b"fine\n")])
    with pytest.raises(ParseError) as exc:
        f1.scrub(data)
    assert "a/blob.bin" in str(exc.value) and "b/other.dat" in str(exc.value)
    assert "notes.txt" not in str(exc.value)


def test_keep_unknown_keeps_it_byte_for_byte_and_says_so():
    blob = bytes(range(256))
    out = f1.scrub(zc.write([Entry(b"blob.bin", blob)]), keep_unknown=True)
    assert _member(out, "blob.bin") == blob
    assert any("NOT scrubbed" in k and "blob.bin" in k for k in f1.kept(out))
    assert f1.residuals(out) == []


def test_an_xmp_sidecar_file_is_metadata_not_text():
    xmp = (b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n<x:xmpmeta '
           b'xmlns:x="adobe:ns:meta/"><GPSLatitude>40,42N</GPSLatitude></x:xmpmeta>')
    with pytest.raises(ParseError, match="photo.xmp"):
        f1.scrub(zc.write([Entry(b"photo.xmp", xmp)]))


def test_text_is_kept_and_reported():
    out = f1.scrub(zc.write([Entry(b"a.csv", b"x,y\n1,2\n")]))
    assert any("a.csv" in k and "kept as written" in k for k in f1.kept(out))


@pytest.mark.parametrize("entries,word", [
    ([Entry(b"[Content_Types].xml", b"<Types/>"), Entry(b"xl/workbook.xml", b"<w/>")],
     "Office Open XML"),
    ([Entry(b"mimetype", b"application/vnd.oasis.opendocument.text", method=0),
      Entry(b"content.xml", b"<x/>")], "OpenDocument"),
    ([Entry(b"META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n")], "Java"),
    ([Entry(b"AndroidManifest.xml", b"\x03\x00")], "Android"),
])
def test_packages_are_refused_by_name(entries, word):
    with pytest.raises(UnsupportedFormatError, match=word):
        f1.scrub(zc.write(entries))


def test_a_member_handlers_refusal_names_the_member():
    broken = zc.photo(1)[:200]                            # a truncated JPEG
    with pytest.raises(ParseError, match="photos/broken.jpg"):
        f1.scrub(zc.write([Entry(b"photos/broken.jpg", broken)]))


def test_a_nested_refusal_names_the_path_through_both_archives():
    inner = zc.write([Entry(b"x.bin", bytes(range(200)))])
    with pytest.raises(ParseError, match="outer/inner.zip/x.bin"):
        f1.scrub(zc.write([Entry(b"outer/inner.zip", inner)]))


# --- names ----------------------------------------------------------------------

def test_a_utf8_name_without_the_flag_is_written_with_it():
    out = f1.scrub(zc.write([Entry("café.txt".encode(), b"x\n")]))
    e = zipread.read(out).entries[0]
    assert e.flags == zipwrite.FLAG_UTF8 and e.name == "café.txt"


def test_a_legacy_code_page_name_is_written_back_as_it_was():
    raw = "café.txt".encode("cp437")                       # not valid UTF-8
    out = f1.scrub(zc.write([Entry(raw, b"x\n")]))
    e = zipread.read(out).entries[0]
    assert e.flags == 0 and e.name_bytes == raw


def _unicode_path(header_name: bytes, utf8: str) -> bytes:
    return zc._x(0x7075, b"\x01" + struct.pack("<I", zlib.crc32(header_name))
                 + utf8.encode())


def test_info_zips_unicode_path_field_names_the_member():
    raw = "café.txt".encode("cp437")
    out = f1.scrub(zc.write([Entry(raw, b"x\n",
                                   extra_cen=_unicode_path(raw, "café.txt"))]))
    assert _names(out) == ["café.txt"]


def test_a_stale_unicode_path_field_is_ignored():
    field = zc._x(0x7075, b"\x01" + struct.pack("<I", 0) + b"other.txt")
    out = f1.scrub(zc.write([Entry(b"real.txt", b"x\n", extra_cen=field)]))
    assert _names(out) == ["real.txt"]


def test_a_traversal_hidden_in_the_unicode_path_field_is_refused():
    with pytest.raises(ParseError, match="relative segment"):
        f1.scrub(zc.write([Entry(b"a.txt", b"x\n",
                                 extra_cen=_unicode_path(b"a.txt", "../a.txt"))]))


# --- links ----------------------------------------------------------------------

def test_a_symbolic_link_stays_a_link_and_an_absolute_target_is_advised():
    data = zc.write([Entry(b"link", b"/Users/someone/Documents/x",
                           external=0o120755 << 16, method=0)])
    out = f1.scrub(data)
    info = zipfile.ZipFile(io.BytesIO(out)).infolist()[0]
    assert info.external_attr >> 16 == zipwrite.MODE_LINK
    assert _member(out, "link") == b"/Users/someone/Documents/x"
    assert any("link" in a and "absolute" in a for a in f1.advise(data))


def test_a_link_out_of_the_archive_is_advised():
    data = zc.write([Entry(b"a/link", b"../../etc/hosts", external=0o120777 << 16,
                           method=0)])
    assert any("outside the archive" in a for a in f1.advise(data))


def test_a_device_file_is_refused():
    with pytest.raises(ParseError, match="special file"):
        f1.scrub(zc.write([Entry(b"dev", b"", external=0o020644 << 16, method=0)]))


def test_home_folder_names_are_advised():
    data = zc.write([Entry(b"Users/someone/Desktop/a.txt", b"x\n")])
    assert any("Users/someone" in a for a in f1.advise(data))


# --- bombs and resources --------------------------------------------------------

def test_a_member_that_inflates_past_its_declared_size_is_refused():
    big = zc.write([Entry(b"a.txt", b"a" * 100_000)])
    # Lower the declared size in both headers: the deflate stream says more.
    lie = big.replace(struct.pack("<I", 100_000), struct.pack("<I", 1000))
    with pytest.raises(ParseError, match="inflates past"):
        f1.scrub(lie)


def test_archives_nested_too_deep_are_refused():
    data = zc.write([Entry(b"a.txt", b"x\n")])
    for i in range(f1.DEPTH_LIMIT + 1):
        data = zc.write([Entry(f"level{i}.zip".encode(), data)])
    with pytest.raises(ParseError, match="nested more than"):
        f1.scrub(data)
    shallow = zc.write([Entry(b"a.txt", b"x\n")])
    for i in range(f1.DEPTH_LIMIT):
        shallow = zc.write([Entry(f"level{i}.zip".encode(), shallow)])
    f1.scrub(shallow)


def test_an_archive_the_machine_cannot_inflate_is_refused(monkeypatch, tmp_path):
    data = zc.build(1, shape="python")
    monkeypatch.setattr(resources, "available_memory", lambda: 1000)
    with pytest.raises(ResourceError, match="inflate to"):
        f1.scrub(data)
    f1.scrub(data, check_memory=False)
    src = tmp_path / "a.zip"
    src.write_bytes(data)
    out = str(tmp_path / "out.zip")
    assert cli.main([str(src), out, "--no-report"]) == 7
    assert not os.path.exists(out)
    assert cli.main([str(src), out, "--no-report", "--skip-memory-check"]) == 0


_PEAK = r"""
import os, resource, sys
sys.path.insert(0, os.environ["REPO"])
from src.scrub.formats.zip import f1
from src.scrub.dispatch import default_dispatcher

def peak():
    if sys.platform.startswith("linux"):
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

data = open(sys.argv[1], "rb").read()
default_dispatcher()
base = peak()
f1.scrub(data, check_memory=False)
print(peak() - base)
"""


def test_memory_need_holds(tmp_path):
    """MEMORY_FACTOR times (archive + declared member sizes) must cover the measured
    peak, and not sit far above it. Random bytes kept as an unknown member would
    need the flag; text members exercise the same path without it."""
    import random
    rng = random.Random(7)
    members = [Entry(f"t{i}.txt".encode(), "".join(
        rng.choice("abcdefghij \n") for _ in range(4_000_000)).encode())
        for i in range(6)]
    data = zc.write(members)
    src = tmp_path / "big.zip"
    src.write_bytes(data)
    r = subprocess.run([sys.executable, "-c", _PEAK, str(src)],
                       env=dict(os.environ, REPO=REPO), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    declared = sum(len(m.body) for m in members)
    measured = int(r.stdout.strip()) / (len(data) + declared)
    assert measured <= f1.MEMORY_FACTOR, f"measured {measured:.2f}x"
    assert f1.MEMORY_FACTOR <= measured * 2.5, f"measured only {measured:.2f}x"


# --- dispatch and the CLI -------------------------------------------------------

def test_dispatch_sends_word_to_docx_and_every_other_archive_here(tmp_path):
    from tests.scrub import docx_corpus as dc
    d = default_dispatcher()
    docx = open(dc.synthetic(str(tmp_path / "a.docx")), "rb").read()
    assert d.resolve(docx).format_id == "docx"
    assert d.resolve(zc.build(1)).format_id == "zip"
    empty = b"PK\x05\x06" + bytes(18)
    assert d.resolve(empty).format_id == "zip"
    assert f1.scrub(empty) == empty


def test_cli_refuses_unknown_members_and_keeps_them_on_request(tmp_path):
    src = tmp_path / "a.zip"
    src.write_bytes(zc.write([Entry(b"blob.bin", bytes(range(256)))]))
    out = str(tmp_path / "out.zip")
    assert cli.main([str(src), out, "--no-report"]) == 4
    assert not os.path.exists(out)
    assert cli.main([str(src), out, "--no-report", "--keep-unknown-members"]) == 0
    assert _member(open(out, "rb").read(), "blob.bin") == bytes(range(256))


def test_the_report_lists_members_and_sidecars(tmp_path):
    src = tmp_path / "a.zip"
    src.write_bytes(zc.build(1, shape="ditto"))
    _, rep = cli.scrub_file_reported(str(src), str(tmp_path / "o.zip"), "F1")
    removed = {i.locus for i in rep.removed}
    assert "photos/photo.jpg: EXIF:Artist" in removed
    assert any(k.startswith("__MACOSX/photos/._photo.jpg") for k in removed)
    assert "entry times (local)" in removed


# --- the shared writer ----------------------------------------------------------

def test_write_archive_refuses_what_it_does_not_write():
    with pytest.raises(ParseError):
        zipwrite.write_archive([zipwrite.Member(b"a", b"x", 0o100600)])
    with pytest.raises(ParseError):
        zipwrite.write_archive([zipwrite.Member(b"a/", b"x", zipwrite.MODE_DIR)])
    with pytest.raises(ParseError):
        zipwrite.write_archive([zipwrite.Member(b"a", b"", zipwrite.MODE_DIR)])
