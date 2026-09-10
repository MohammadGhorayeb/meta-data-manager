"""The independent check: ExifTool, not us.

The scrub report is this tool's account of its own work, and a handler bug that
failed to remove a field would also fail to report it — both come from the same
walker. So the report ends with a reading by a different implementation.

The tests are aimed at the one thing that makes it worth having: it must be able to
**disagree**. A check that can only ever agree is decoration.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from src.scrub import cli, crosscheck

pytestmark = pytest.mark.skipif(not crosscheck.available(),
                                reason="exiftool absent")


@pytest.fixture
def photo(tmp_path):
    from . import corpus
    p = tmp_path / "in.jpg"
    p.write_bytes(corpus.build_torture_jpeg())
    return str(p)


# --------------------------------------------------------------------------- #
# It can disagree
# --------------------------------------------------------------------------- #
def test_it_catches_a_value_the_report_claimed_was_removed(photo):
    """The check that can fail, and the reason the module exists.

    Comparing the file against *itself* stands in for a handler that removed
    nothing: every value is still readable, and every one must be named.
    """
    check = crosscheck.run(photo, photo, ["TestCam", "secret-app 1.0"])
    assert check.leaked == ["TestCam", "secret-app 1.0"]
    text = "\n".join(crosscheck.render(check))
    assert "still readable in the output" in text
    assert "incomplete" in text


def test_a_real_scrub_leaks_nothing_and_says_so(photo, tmp_path):
    out = str(tmp_path / "out.jpg")
    _, report = cli.scrub_file_reported(photo, out, "F1",
                                        verify_with_exiftool=True)
    assert report.check is not None
    assert report.check.leaked == []
    assert "none of the removed values appear" in "\n".join(
        crosscheck.render(report.check))


def test_whole_standards_are_reported_gone(photo, tmp_path):
    """The honest headline: not "we removed 17 things" but "GPS and IPTC are gone",
    said by something that is not us."""
    out = str(tmp_path / "out.jpg")
    _, report = cli.scrub_file_reported(photo, out, "F1",
                                        verify_with_exiftool=True)
    gone = set(report.check.gone_groups)
    assert {"GPS", "IPTC", "ExifIFD"} <= gone, gone
    assert len(report.check.after) < len(report.check.before)


# --------------------------------------------------------------------------- #
# It reads what is actually there
# --------------------------------------------------------------------------- #
def test_derived_tags_are_not_counted_as_metadata(photo):
    """ExifTool reports its own version, the filesystem's view of the file, values
    decoded from the image structure, and its computed composites. None is stored
    metadata; counting them would make every file look dirty forever."""
    tags, err = crosscheck.read_tags(photo)
    assert tags and not err
    assert not [k for k in tags if crosscheck.group_of(k) in
                crosscheck.DERIVED_GROUPS]
    assert not any("FileName" in k or "FileSize" in k for k in tags)


def test_duplicate_tags_are_not_collapsed(tmp_path):
    """A DOCX has one set of ZIP tags per part. Plain `-G1` JSON keys collide, so
    they collapse onto one entry — a scrubbed package read as 8 tags instead of 72,
    and a value hiding in a collapsed duplicate could escape the leak search."""
    from . import docx_corpus as dc
    src = dc.synthetic(str(tmp_path / "in.docx"))
    tags, _ = crosscheck.read_tags(src)
    names = [k for k in tags if k.endswith("ZipFileName")]
    assert len(names) > 1, "duplicate ZIP entries collapsed onto one key"


def test_short_values_are_not_searched_for(photo, tmp_path):
    """`1`, `N`, `RGB` appear in almost any file's structure, so searching for them
    would report a leak on every clean scrub."""
    out = str(tmp_path / "out.jpg")
    cli.scrub_file(photo, out, "F1")
    check = crosscheck.run(photo, out, ["N", "1", "RGB", "8"])
    assert check.leaked == []


def test_byte_count_placeholders_are_not_searched_for(photo, tmp_path):
    """The report writes `(614 bytes)` where a value is a blob. Searching for that
    literal would match nothing useful and could match a size ExifTool prints."""
    out = str(tmp_path / "out.jpg")
    cli.scrub_file(photo, out, "F1")
    assert crosscheck.run(photo, out, ["(614 bytes)"]).leaked == []


# --------------------------------------------------------------------------- #
# The command handed to the user
# --------------------------------------------------------------------------- #
def test_the_printed_command_actually_runs(photo, tmp_path):
    """A "check it yourself" instruction that does not work is worse than none."""
    import shlex
    out = str(tmp_path / "out.jpg")
    cli.scrub_file(photo, out, "F1")
    command = crosscheck.command_for(out)
    proc = subprocess.run(shlex.split(command), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "JFIF" in proc.stdout


def test_a_path_with_spaces_is_quoted(tmp_path):
    """Otherwise the command breaks on exactly the files a person keeps in
    `~/Documents/My Report.docx`."""
    weird = str(tmp_path / "a file with spaces.jpg")
    assert "'" in crosscheck.command_for(weird) or '"' in crosscheck.command_for(weird)


def test_the_render_always_ends_with_the_command(photo):
    """The point of the block is that the reader does not have to believe the lines
    above it."""
    check = crosscheck.run(photo, photo, [])
    lines = crosscheck.render(check)
    assert lines[-1].strip().startswith("exiftool")


# --------------------------------------------------------------------------- #
# It cannot break a scrub
# --------------------------------------------------------------------------- #
def test_a_missing_exiftool_still_tells_the_user_how_to_check(monkeypatch, tmp_path):
    monkeypatch.setattr(crosscheck, "available", lambda: False)
    check = crosscheck.run("in", "out", ["x"])
    text = "\n".join(crosscheck.render(check))
    assert "not installed" in text
    assert "exiftool" in text.split("Check it yourself:")[1]


def test_a_file_with_no_metadata_reads_empty_without_an_error(tmp_path):
    """ExifTool reads a junk file happily and finds nothing embedded in it. That is a
    genuine empty result, not a failure, and must not be reported as one."""
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"\x00\x01\x02")
    tags, err = crosscheck.read_tags(str(junk))
    assert tags == {} and err == ""


def test_a_failed_read_is_not_rendered_as_a_clean_result(tmp_path):
    """The worst failure this module could have.

    A read that returned nothing used to render as `0 tags before → 0 after, none of
    the removed values appear` — which is exactly what a clean scrub looks like. A
    check that reassures on its own failure is worse than no check at all.
    """
    missing = str(tmp_path / "does-not-exist.jpg")
    check = crosscheck.run(missing, missing, ["anything"])
    text = "\n".join(crosscheck.render(check))
    assert "NOT a clean result" in text
    assert "none of the removed values" not in text
    assert check.leaked == [], "no leak claim can be made from a failed read"


def test_a_leading_dash_filename_is_still_read(tmp_path):
    """A relative path beginning with `-` is parsed by ExifTool as an OPTION, so the
    read silently returned nothing — and, before the previous test existed, rendered
    as a clean result. Absolute paths fix it at the source."""
    from . import corpus
    src = tmp_path / "-photo.jpg"
    src.write_bytes(corpus.build_torture_jpeg())

    import os
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        tags, err = crosscheck.read_tags("-photo.jpg")
    finally:
        os.chdir(cwd)
    assert tags and not err, err


def test_the_printed_command_is_absolute(tmp_path):
    """So it works from whatever directory the reader is in, not only the one the
    scrub happened to run in."""
    assert crosscheck.command_for("out.jpg").rstrip("'\"").endswith("out.jpg")
    assert "/" in crosscheck.command_for("out.jpg")


def test_the_check_runs_after_the_output_is_written(photo, tmp_path):
    """Ordering asserted on the code path: nothing the check does can affect whether
    the scrub succeeded."""
    import inspect
    source = inspect.getsource(cli.scrub_file_reported)
    assert source.index("_write_atomic") < source.index("crosscheck.run")


def test_no_verify_skips_it(photo, tmp_path, capsys):
    assert cli.main([photo, str(tmp_path / "o.jpg"), "--no-verify"]) == 0
    out = capsys.readouterr().out
    assert "JPEG · F1" in out                      # the report still prints
    assert "Check it yourself" not in out


def test_verification_is_on_by_default(photo, tmp_path, capsys):
    assert cli.main([photo, str(tmp_path / "o.jpg")]) == 0
    assert "Check it yourself" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool absent")
def test_every_supported_format_is_cross_checked(tmp_path):
    """A format that silently skipped the check would leave its users with only our
    own word for it."""
    from . import corpus
    from . import docx_corpus as dc
    from . import pdf_corpus as pc
    from .test_png import _png

    cases = {
        "jpg": corpus.build_torture_jpeg(),
        "png": _png(),
        "docx": open(dc.synthetic(str(tmp_path / "s.docx")), "rb").read(),
        "pdf": open(pc.incremental_pdf(str(tmp_path / "s.pdf")), "rb").read(),
    }
    for ext, blob in cases.items():
        src = tmp_path / f"in.{ext}"
        src.write_bytes(blob)
        _, report = cli.scrub_file_reported(
            str(src), str(tmp_path / f"out.{ext}"), "F1",
            verify_with_exiftool=True)
        assert report.check is not None and report.check.available, ext
        assert report.check.leaked == [], (ext, report.check.leaked)


def test_a_long_value_is_searched_for_in_full(tmp_path):
    """The clipping bug, as a test.

    The report shortens values for display. Feeding those shortened strings to the
    leak search meant searching for something ending in `…`, which can never match —
    so the check was silently disabled for every value over the clip limit. Those are
    exactly the identifying ones: a full name with an affiliation, an absolute path.
    """
    from src.scrub import report as rep
    from src.scrub.dispatch import default_dispatcher

    long_value = ("Mohammad Ghorayeb, Department of Electrical and Computer "
                  "Engineering, AUB")
    assert len(long_value) > rep.MAX_VALUE, "the value must be long enough to clip"

    import piexif
    from PIL import Image
    src = tmp_path / "long.jpg"
    Image.new("RGB", (64, 48), (120, 90, 60)).save(
        str(src), "JPEG",
        exif=piexif.dump({"0th": {piexif.ImageIFD.Artist: long_value.encode()}}))

    data = src.read_bytes()
    handler = default_dispatcher().resolve(data)
    # before == after stands in for a handler that removed nothing.
    report = rep.build(handler, data, data, "F1")

    item = next(i for i in report.items if "Artist" in i.locus)
    assert item.before.endswith("…"), "display value should be clipped"
    assert item.raw_before == long_value, "the raw value must survive for the search"

    check = crosscheck.run(str(src), str(src),
                           [i.raw_before or i.before for i in report.items])
    assert any(long_value in leaked for leaked in check.leaked), \
        "a long value that is still present must be caught"
