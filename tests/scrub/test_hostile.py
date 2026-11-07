"""Files built to hurt the tool (November plan N2b): each refused, or cleaned within
bounds, and never written in part.

The fuzz suite damages files at random; these are made on purpose. Each row was
probed through the CLI first (docs/november_plan.md §N2b): five already held, and
seven did not -- a 1 MB Word file declaring a 1 GiB part ran past 120 s and 6.5 GB;
a PNG whose 1 MB text chunk inflates to 1 GiB took 52 s and 6 GB to *describe*;
JPEG's full clean let Pillow's bomb refusal escape as "unexpected" and its deep clean
spent 16 s on a 3.6-billion-pixel header; twelve external-program calls had no time
limit; `scrub a.jpg a.jpg` replaced the original; and a folder as the output failed
as "unexpected". Sizes here are smaller than in the probes; the memory budget is
injected, so the verdict does not depend on the machine.
"""
from __future__ import annotations

import io
import os
import signal
import struct
import sys
import time
import zipfile
import zlib

import pytest

from src.scrub import cli, resources
from src.scrub.errors import ParseError, ResourceError, ScrubError, ToolError

MiB = 1 << 20


@pytest.fixture
def within(request):
    """Fail a row that takes longer than its bound, rather than hang the suite."""
    def bound(seconds: float):
        def alarm(_s, _f):
            raise TimeoutError(f"took longer than {seconds} s")
        signal.signal(signal.SIGALRM, alarm)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        request.addfinalizer(lambda: signal.setitimer(signal.ITIMER_REAL, 0))
    return bound


def _refuse(tmp_path, data: bytes, ext: str, fidelity: str = "F1") -> ScrubError:
    src, dst = tmp_path / f"in{ext}", tmp_path / f"out{ext}"
    src.write_bytes(data)
    with pytest.raises(ScrubError) as caught:
        cli.scrub_file(str(src), str(dst), fidelity)
    assert cli.exit_code(caught.value) != 1, caught.value
    assert not dst.exists() and sorted(os.listdir(tmp_path)) == [src.name]
    return caught.value


def _with_member(package: bytes, name: str, body: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(package)) as zin, \
            zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info, zin.read(info))
        zout.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), body,
                      compress_type=zipfile.ZIP_DEFLATED, compresslevel=1)
    return out.getvalue()


# --------------------------------------------------------------------------- #
# Decompression: priced from DECLARED sizes, before anything is inflated
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fmt", ["docx", "xlsx", "pptx"])
def test_an_office_part_that_would_not_fit_in_memory_is_refused_up_front(
        fmt, tmp_path, monkeypatch, within):
    """64 MiB of XML is priced at ~1.8 GB (28x, measured); with 1 GB free it is
    refused in a moment, saying what it needs."""
    from . import docx_corpus as dc
    from . import pptx_corpus as pc
    from . import xlsx_corpus as xc
    base = {"docx": lambda: open(dc.synthetic(str(tmp_path / "s.docx")), "rb").read(),
            "xlsx": lambda: xc.build(1), "pptx": lambda: pc.build(1)}[fmt]()
    (tmp_path / "s.docx").unlink(missing_ok=True)
    data = _with_member(base, "customXml/item9.xml",
                        b'<?xml version="1.0"?><x>' + b" " * (64 * MiB) + b"</x>")
    monkeypatch.setattr(resources, "available_memory", lambda: 10**9)
    within(10)
    exc = _refuse(tmp_path, data, f".{fmt}")
    assert isinstance(exc, ResourceError) and "of XML" in str(exc)


def test_an_archive_member_that_would_not_fit_in_memory_is_refused_up_front(
        tmp_path, monkeypatch, within):
    from . import zip_corpus as zc
    data = _with_member(zc.build(1, shape="python"), "notes.txt", b"A" * (64 * MiB))
    monkeypatch.setattr(resources, "available_memory", lambda: 10**8)
    within(10)
    assert isinstance(_refuse(tmp_path, data, ".zip"), ResourceError)


def test_archives_nested_past_the_limit_are_refused(tmp_path, within):
    inner, name = b"hello\n", "a.txt"
    for depth in range(12):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(name, inner)
        inner, name = buf.getvalue(), f"level{depth}.zip"
    within(10)
    exc = _refuse(tmp_path, inner, ".zip")
    assert isinstance(exc, ParseError) and "nested" in str(exc)


def _png(width: int, height: int, extra: bytes = b"", idat: bytes | None = None) -> bytes:
    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
    if idat is None:
        idat = zlib.compress(b"".join(b"\0" + b"\x80" * width for _ in range(height)))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height,
                                                              8, 0, 0, 0, 0))
            + extra + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def test_a_png_text_chunk_that_inflates_to_gigabytes_is_described_in_a_moment(
        tmp_path, within):
    """The light clean drops a zTXt chunk without inflating it; the report used to
    inflate it whole just to print one clipped line."""
    from src.scrub.formats.png import inspect
    co = zlib.compressobj(9)
    z = b"".join(co.compress(b"A" * MiB) for _ in range(256)) + co.flush()
    body = b"Comment\0\0" + z
    data = _png(4, 4, struct.pack(">I", len(body)) + b"zTXt" + body
                + struct.pack(">I", zlib.crc32(b"zTXt" + body)))
    within(10)
    value = inspect.describe(data)["zTXt:Comment"]
    assert value.endswith("[...]") and len(value) < 70_000
    src, dst = tmp_path / "in.png", tmp_path / "out.png"
    src.write_bytes(data)
    cli.scrub_file(str(src), str(dst), "F1")
    assert b"zTXt" not in dst.read_bytes()


# --------------------------------------------------------------------------- #
# Pictures that declare more pixels than can be decoded
# --------------------------------------------------------------------------- #
def test_a_png_declaring_ten_billion_pixels_is_a_resource_refusal_where_decoded(
        tmp_path, within):
    co = zlib.compressobj(9)
    rows = b"".join(co.compress(b"\0" * 100_001) for _ in range(2000)) + co.flush()
    data = _png(100_000, 100_000, idat=rows)
    within(20)
    assert isinstance(_refuse(tmp_path, data, ".png", "F2"), ResourceError)


@pytest.mark.parametrize("fidelity", ["F2", "F3"])
def test_a_jpeg_declaring_billions_of_pixels_is_refused_before_any_work(
        fidelity, tmp_path, within):
    from src.scrub.formats.jpeg import segments as seg

    from . import corpus
    data = bytearray(corpus.make_base_jpeg())
    sof = next(s for s in seg.walk(bytes(data)).segments if s.kind.startswith("sof"))
    data[sof.offset + 5:sof.offset + 9] = struct.pack(">HH", 60000, 60000)
    within(5)
    exc = _refuse(tmp_path, bytes(data), ".jpg", fidelity)
    assert isinstance(exc, ResourceError) and "light clean" in str(exc)


# --------------------------------------------------------------------------- #
# Markup built to expand, or to nest without end
# --------------------------------------------------------------------------- #
def test_an_svg_billion_laughs_is_refused(tmp_path, within):
    ents = "".join(f'<!ENTITY {chr(98 + i)} "' + f"&{chr(97 + i)};" * 10 + '">\n'
                   for i in range(9))
    data = ('<?xml version="1.0"?>\n<!DOCTYPE svg [\n<!ENTITY a "lol">\n' + ents
            + ']>\n<svg xmlns="http://www.w3.org/2000/svg"><text>&j;</text></svg>').encode()
    within(5)
    assert isinstance(_refuse(tmp_path, data, ".svg"), ParseError)


def test_an_svg_nested_a_hundred_thousand_deep_cleans_within_bounds(tmp_path, within):
    data = ('<svg xmlns="http://www.w3.org/2000/svg">' + "<g>" * 100_000
            + "</g>" * 100_000 + "</svg>").encode()
    src, dst = tmp_path / "in.svg", tmp_path / "out.svg"
    src.write_bytes(data)
    within(20)
    cli.scrub_file(str(src), str(dst), "F1")
    assert dst.exists()


# --------------------------------------------------------------------------- #
# Counts and offsets that promise more than the file holds
# --------------------------------------------------------------------------- #
def test_a_tiff_declaring_four_billion_strips_is_refused(tmp_path, within):
    t = bytearray(b"II*\0" + struct.pack("<I", 8) + struct.pack("<H", 4))
    for tag, typ, cnt, val in ((256, 4, 1, 16), (257, 4, 1, 16),
                               (273, 4, 0xFFFFFFFF, 64), (279, 4, 0xFFFFFFFF, 64)):
        t += struct.pack("<HHII", tag, typ, cnt, val)
    within(5)
    assert isinstance(_refuse(tmp_path, bytes(t + b"\0" * 300), ".tif"), ParseError)


# --------------------------------------------------------------------------- #
# Programs that never finish, and paths that would destroy the original
# --------------------------------------------------------------------------- #
def test_an_external_program_that_never_finishes_is_stopped_and_named(monkeypatch, within):
    from src.scrub import tools
    monkeypatch.setattr(tools, "BASE_SECONDS", 0.5)
    within(10)
    start = time.monotonic()
    with pytest.raises(ToolError, match="ran past"):
        tools.run([sys.executable, "-c", "import time; time.sleep(30)"],
                  capture_output=True)
    assert time.monotonic() - start < 5


def test_the_output_may_not_be_the_input(tmp_path, capsys):
    from . import corpus
    src = tmp_path / "a.jpg"
    original = corpus.make_base_jpeg()
    src.write_bytes(original)
    assert cli.main([str(src), str(src), "--no-report"]) == 2
    assert src.read_bytes() == original
    link = tmp_path / "link.jpg"
    link.symlink_to(src)
    assert cli.main([str(src), str(link), "--no-report"]) == 2
    assert "the input itself" in capsys.readouterr().err


def test_a_folder_as_the_output_is_a_usage_error(tmp_path, capsys):
    from . import corpus
    src = tmp_path / "a.jpg"
    src.write_bytes(corpus.make_base_jpeg())
    (tmp_path / "out").mkdir()
    assert cli.main([str(src), str(tmp_path / "out"), "--no-report"]) == 2
    assert os.listdir(tmp_path / "out") == []
