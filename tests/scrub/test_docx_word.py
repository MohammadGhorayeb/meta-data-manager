"""Word's own loci (`docs/word_plan.md`, W1): what Microsoft Word writes that the
Phase 3 producers never did. Each was a probe first -- two of them measured passing
through F1 untouched (a tracked formatting change's reviewer, an editing
permission's e-mail address)."""
from __future__ import annotations

import pytest

from src.scrub.errors import ContentError
from src.scrub.formats.docx import f1, f2
from tests.scrub import word_corpus as wc


def _removable(**kw) -> bytes:
    """Every W1 locus F1 removes (not the ones it refuses)."""
    return wc.build(kw.pop("variant", 1),
                    loci=("permission", "settings", "webextensions",
                          "printer_settings"))


@pytest.mark.parametrize("locus", ["permission", "settings", "webextensions",
                                   "printer_settings"])
def test_f1_removes_words_hidden_parts_and_elements(locus):
    data = wc.build(1, loci=(locus,))
    planted = wc.planted(1, locus)
    assert all(any(p in b for b in wc.members(data).values()) for p in planted)
    out = f1.scrub(data)
    assert not [p for p in planted if any(p in b for b in wc.members(out).values())]
    assert f1.residuals(out) == []


def test_the_text_between_permission_markers_stays():
    out = wc.members(f1.scrub(wc.build(1, loci=("permission",))))
    doc = out["word/document.xml"]
    assert b"editable part" in doc and b"permStart" not in doc


def test_dropped_parts_leave_a_consistent_package():
    out = wc.members(f1.scrub(_removable()))
    assert not [n for n in out if n.startswith(("word/webextensions/",
                                                "word/printerSettings/"))]
    rels = out["_rels/.rels"] + out.get("word/_rels/document.xml.rels", b"")
    assert b"webextension" not in rels and b"printerSettings" not in rels
    assert b"webextension" not in out["[Content_Types].xml"]


def test_settings_keep_what_is_not_metadata():
    settings = wc.members(f1.scrub(wc.build(1, loci=("settings",))))["word/settings.xml"]
    assert b"defaultTabStop" in settings and b"compatibilityMode" in settings
    assert b"writeProtection" not in settings and b"docVars" not in settings


def test_variants_differing_only_in_these_loci_clean_to_the_same_bytes():
    assert len({f1.scrub(_removable(variant=v)) for v in (1, 2, 3)}) == 1


def test_a_tracked_formatting_change_is_refused_at_f1_and_accepted_at_f2():
    data = wc.build(1, loci=("formatting_change",))
    with pytest.raises(ContentError, match="tracked formatting change"):
        f1.scrub(data)
    out = wc.members(f2.scrub(data))["word/document.xml"]
    assert wc.secret("REVIEWER", 1).encode() not in out
    assert b"rPrChange" not in out and b"bold now" in out and b"<w:b/>" in out


@pytest.mark.parametrize("tag", ["w:cellDel", "w:cellMerge"])
def test_table_cell_deletions_and_merges_are_refused_even_at_f2(tag):
    data = wc.build(1, loci=())
    members = wc.members(data)
    members["word/document.xml"] = members["word/document.xml"].replace(
        b"</w:body>", f'<w:tbl><w:tr><w:tc><w:tcPr><{tag} w:id="3" w:author="x" '
        f'w:date="2026-01-01T00:00:00Z"/></w:tcPr><w:p/></w:tc></w:tr></w:tbl>'
        "</w:body>".encode())
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in members.items():
            z.writestr(n, b)
    with pytest.raises(ContentError, match="rebuilding the table"):
        f2.scrub(buf.getvalue())


def test_activex_controls_are_refused():
    with pytest.raises(ContentError, match="ActiveX"):
        f1.scrub(wc.build(1, loci=("activex",)))


def test_every_picture_is_cleaned_by_its_own_handler():
    """W2: GIF, TIFF, WebP and SVG were refused outright, by extension. Each corpus
    plants SENTINEL values in its own loci (comments, XMP, EXIF, editor names)."""
    data = wc.build(1, loci=("pictures",))
    media = {n: b for n, b in wc.members(data).items() if n.startswith("word/media/")}
    assert len(media) == 4 and all(b"SENTINEL" in b for b in media.values())
    out = {n: b for n, b in wc.members(f1.scrub(data)).items()
           if n.startswith("word/media/")}
    assert sorted(out) == sorted(media)
    assert not [n for n, b in out.items() if b"SENTINEL" in b]
    assert f1.residuals(f1.scrub(data)) == []


def test_jpeg_and_png_still_clean_exactly_as_before():
    from src.scrub.formats.jpeg import f1 as jpeg_f1
    from src.scrub.formats.png import f1 as png_f1
    from tests.scrub import zip_corpus as zc
    assert f1._scrub_media("word/media/a.jpeg", zc.photo(1)) == jpeg_f1.scrub(zc.photo(1))
    assert f1._scrub_media("word/media/b.png", zc.png(1)) == png_f1.scrub(zc.png(1))


def test_a_picture_is_identified_by_content_not_by_its_name():
    from tests.scrub import zip_corpus as zc
    renamed = f1._scrub_media("word/media/image1.png", zc.photo(1))   # a JPEG
    assert renamed[:2] == b"\xff\xd8" and zc.secret("PHOTO-ARTIST", 1).encode() not in renamed


def test_emf_and_wmf_pictures_are_cleaned_by_their_own_handler():
    """W2b: refused by name until the metafile handler existed (limit #68)."""
    data = wc.build(1, loci=("emf",))
    media = {n: b for n, b in wc.members(data).items() if n.startswith("word/media/")}
    assert all(b"SENTINEL" in b or "SENTINEL".encode("utf-16-le") in b
               for b in media.values())
    out = {n: b for n, b in wc.members(f1.scrub(data)).items()
           if n.startswith("word/media/")}
    assert sorted(out) == sorted(media)
    assert not [n for n, b in out.items()
                if b"SENTINEL" in b or "SENTINEL".encode("utf-16-le") in b]
    assert f1.residuals(f1.scrub(data)) == []


# --- W1b: measured on 784 real Word documents (docs/word_plan.md §5) ---------------

def test_template_and_mail_merge_paths_go_with_their_elements():
    data = wc.build(1, loci=("template",))
    out = wc.members(f1.scrub(data))
    assert b"attachedTemplate" not in out["word/settings.xml"]
    assert "word/_rels/settings.xml.rels" not in out          # nothing left in it
    assert not [n for n, b in out.items() if wc.secret("USER", 1).encode() in b]
    assert f1.residuals(f1.scrub(data)) == []


def test_picture_file_names_and_paths_are_blanked_and_descriptions_kept():
    out = wc.members(f1.scrub(wc.build(1, loci=("drawing",))))
    doc = out["word/document.xml"]
    assert wc.secret("USER", 1).encode() not in doc and b"IMG_2034" not in doc
    assert b'name="Picture 1"' in doc and b'alt="A short caption"' in doc
    assert b'descr=""' in doc and b'name=""' in doc


def test_an_embedded_picture_loses_its_link_and_the_link_relationship():
    out = wc.members(f1.scrub(wc.build(1, loci=("drawing",))))
    doc, rels = out["word/document.xml"], out["word/_rels/document.xml.rels"]
    assert b'r:embed="rId20"' in doc and b"r:link" not in doc and b"o:href" not in doc
    assert b'Id="rId21"' not in rels and b'Id="rId20"' in rels


def test_a_picture_that_is_only_linked_keeps_its_link():
    from src.scrub.formats.docx.f1 import _drop_linked_paths
    only = b'<a:blip r:link="rId21"/>'
    assert _drop_linked_paths(only) == only


def test_the_cursor_bookmark_goes_with_its_end_marker():
    doc = wc.members(f1.scrub(wc.build(1, loci=("goback",))))["word/document.xml"]
    assert b"_GoBack" not in doc and b"bookmarkEnd" not in doc
    assert b"after the cursor" in doc


def test_f2_applies_the_same_rules():
    data = wc.build(1, loci=("template", "drawing", "goback"))
    out = wc.members(f2.scrub(data))
    assert not [n for n, b in out.items() if wc.secret("USER", 1).encode() in b]
    assert not [n for n, b in out.items() if b"_GoBack" in b or b"IMG_2034" in b]
