"""The scrub report: what was in the file, and what is in it now.

Two things are tested here that are easy to get wrong in a reporting path:

- it must never be able to fail a good scrub, and
- it must not overstate what it knows. The report shows what *this tool* saw, and a
  locus no handler models is absent from it — so "nothing listed" can never be
  allowed to read as "nothing was there".
"""
from __future__ import annotations

import pytest

from src.scrub import cli
from src.scrub import report as rep


def _describe_of(fmt: str, data: bytes) -> dict[str, str]:
    from src.scrub.dispatch import default_dispatcher
    return default_dispatcher().resolve(data).describe(data)


# --------------------------------------------------------------------------- #
# The diff
# --------------------------------------------------------------------------- #
class _Fake:
    format_id = "fake"

    def __init__(self, before, after):
        self._b, self._a = before, after

    def describe(self, data):
        return self._b if data == b"before" else self._a


def test_removed_changed_kept_and_added_are_each_reported():
    handler = _Fake({"A": "1", "B": "2", "C": "3"},
                    {"B": "two", "C": "3", "D": "4"})
    r = rep.build(handler, b"before", b"after", "F1")
    got = {i.locus: i.status for i in r.items}
    assert got == {"A": rep.REMOVED, "B": rep.CHANGED,
                   "C": rep.KEPT, "D": rep.ADDED}
    changed = next(i for i in r.items if i.locus == "B")
    assert changed.before == "2" and changed.after == "two"


def test_the_report_reads_in_the_files_own_order():
    """Alphabetical order would scramble a file's structure; the input's order is
    how a person reading the bytes would meet the fields."""
    handler = _Fake({"z": "1", "a": "2", "m": "3"}, {})
    assert [i.locus for i in rep.build(handler, b"before", b"after", "F1").items] \
        == ["z", "a", "m"]


def test_a_handler_that_cannot_describe_itself_says_so():
    class Silent:
        format_id = "silent"

    r = rep.build(Silent(), b"x", b"y", "F1")
    assert r.described is False
    assert "cannot yet describe" in rep.render(r)


def test_a_describe_that_raises_cannot_fail_the_report():
    """A display feature must never turn a successful scrub into a failure."""
    class Broken:
        format_id = "broken"

        def describe(self, data):
            raise RuntimeError("boom")

    r = rep.build(Broken(), b"x", b"y", "F1")
    assert r.described is False
    rep.render(r)          # must not raise


def test_values_are_clipped_and_stripped_of_control_characters():
    """A crafted file must not be able to write escape sequences to the terminal of
    someone auditing it, and an XMP packet must not fill the screen with the data we
    just removed."""
    assert rep.clip("a" * 200).endswith("…")
    assert len(rep.clip("a" * 200)) <= rep.MAX_VALUE
    assert "\x1b" not in rep.clip("\x1b[31mred\x1b[0m")
    assert rep.clip("two\nlines\twith\tgaps") == "two lines with gaps"


def test_render_never_claims_the_file_was_empty_of_metadata():
    """The wording matters more than it looks: this tool's coverage is not the same
    as the file's contents, and a report that said "no metadata" would be making a
    claim only `exiftool` can make."""
    handler = _Fake({}, {})
    text = rep.render(rep.build(handler, b"before", b"after", "F1"))
    assert "not the same as none being present" in text


# --------------------------------------------------------------------------- #
# Real formats, through the real CLI
# --------------------------------------------------------------------------- #
def test_jpeg_report_shows_the_camera_the_time_and_the_place(tmp_path, capsys):
    from . import corpus
    src = tmp_path / "in.jpg"
    src.write_bytes(corpus.build_torture_jpeg())
    assert cli.main([str(src), str(tmp_path / "out.jpg"), "--fidelity", "F1"]) == 0

    out = capsys.readouterr().out
    assert "JPEG · F1" in out
    for expected in ("EXIF:Make", "TestCam", "EXIF:GPSLatitude",
                     "EXIF:DateTimeOriginal", "JPEG comment"):
        assert expected in out, expected
    assert "removed" in out


def test_the_report_shows_what_survived_as_well_as_what_went(tmp_path, capsys):
    """MP3's LAME header cannot be removed without re-encoding (limit #1), so an F1
    scrub keeps it. A report that only listed removals would let a user believe the
    file was fully anonymised."""
    from . import mp3_corpus as mc
    if not mc.HAVE_FFMPEG:
        pytest.skip("ffmpeg absent")
    src = mc.torture_mp3(str(tmp_path / "in.mp3"))
    assert cli.main([src, str(tmp_path / "out.mp3"), "--fidelity", "F1"]) == 0
    out = capsys.readouterr().out
    assert "ID3:Title" in out
    assert "kept" in out and "LAME" in out


def test_pdf_report_names_the_revision_history(tmp_path, capsys):
    """The headline leak of the format. A user who sees "3 revisions — earlier drafts
    are still in the file" has learned something no tag list would have told them."""
    from . import pdf_corpus as pc
    src = pc.incremental_pdf(str(tmp_path / "in.pdf"))
    assert cli.main([src, str(tmp_path / "out.pdf"), "--fidelity", "F1"]) == 0
    out = capsys.readouterr().out
    assert "revisions" in out and "earlier drafts" in out
    assert "Author-FINAL-SECRET" in out


def test_docx_report_counts_the_session_ids(tmp_path, capsys):
    """Listing 36 opaque hex ids would bury everything a person can read, so the
    session-id family is counted."""
    from . import e_session_id as es
    # Not the torture package: it also carries macros and an OLE object, which every
    # tier refuses structurally, so the scrub would never run. This one has the whole
    # session-id family and nothing that gets refused.
    src = es.word_like(str(tmp_path / "in.docx"))
    assert cli.main([src, str(tmp_path / "out.docx"), "--fidelity", "F1"]) == 0
    out = capsys.readouterr().out
    assert "w:rsid" in out and "occurrence(s)" in out
    assert "docId" in out and "paraId" in out


def test_no_report_suppresses_it_but_keeps_the_warnings(tmp_path, capsys):
    from . import corpus
    src = tmp_path / "in.jpg"
    src.write_bytes(corpus.build_torture_jpeg())
    assert cli.main([str(src), str(tmp_path / "o.jpg"), "--no-report"]) == 0
    assert capsys.readouterr().out == ""


def test_the_report_does_not_change_the_exit_code(tmp_path):
    """Asserted on the code path: the report is built after the output is written, so
    nothing it does can affect whether the scrub succeeded."""
    import inspect
    source = inspect.getsource(cli.scrub_file_reported)
    write_at = source.index("_write_atomic")
    assert write_at < source.index("rep.build"), \
        "the report must be built after the output is written"


@pytest.mark.parametrize("fmt", ["jpeg", "png", "mp3", "flac", "m4a", "pdf", "docx"])
def test_every_registered_handler_can_describe_itself(fmt):
    """A format that ships without a describe() would print "cannot yet describe" to
    every user of it, which is worse than not having the feature."""
    from src.scrub.dispatch import default_dispatcher
    handler = next(h for h in default_dispatcher()._handlers
                   if h.format_id == fmt)
    assert callable(getattr(handler, "describe", None)), fmt
