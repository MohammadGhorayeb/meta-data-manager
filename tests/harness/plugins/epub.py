"""EpubPlugin — harness-side format knowledge for EPUB.

Content identity is the book as read: the words of every chapter in spine order
and the decoded pixels of every picture. `structural_features` is the A2 channel
in three parts, each prefixed so E-EPUB can report them apart:

- `pkg_`: the packager -- the ZIP's version fields, flags, extra fields,
  attributes, compression methods and entry order;
- `meta_`: the package metadata -- which elements the OPF's metadata holds and in
  what order, the package element's attributes, the identifier's form, the NCX
  head;
- `content_`: the content model -- the file layout, how chapters' heads are built,
  the XML declaration, the class vocabulary, the stylesheet's size.

The guard's view is the container's headers plus the decompressed XML parts (where
F1's edits are); pictures and fonts are guarded in their own matrices.
"""
from __future__ import annotations

import hashlib
import io
import re
import zlib

from src.scrub.formats.epub import f1, ocf
from src.scrub.formats.ooxml import xmlspans as xs
from src.scrub.formats.ooxml import zipread
from tests.harness.plugins.zip import ZipPlugin

CHANNELS = ("pkg_", "meta_", "content_")


def _book(path: str):
    try:
        return f1.Book(open(path, "rb").read(), check_memory=False)
    except Exception:                                     # noqa: BLE001
        return None


def _spine(book) -> list[str]:
    els = book.package.els
    by_id = {path_id: path for path, (path_id, _t) in book.package.manifest.items()}
    return [by_id[el.attr("idref")] for el in els
            if el.local == "itemref" and el.attr("idref") in by_id]


def words(book) -> str:
    """The text of every chapter in spine order, tags removed, whitespace folded."""
    out = []
    for name in _spine(book):
        body = book.body(name)
        start = body.find(b">", body.find(b"<body")) + 1
        text = re.sub(rb"<[^>]+>", b" ", body[start:])
        out.append(b" ".join(text.split()).decode("utf-8", "replace"))
    return " ".join(w for w in out if w)


class EpubPlugin:
    format_id = "epub"

    def matches(self, header: bytes, path: str = "") -> bool:
        return header[:4] == b"PK\x03\x04"

    def annotate(self, in_path: str, offset: int) -> str | None:
        return ZipPlugin().annotate(in_path, offset)

    def canonical_content(self, path: str) -> bytes:
        book = _book(path)
        if book is None:
            return b""
        h = hashlib.sha256()
        h.update(words(book).encode())
        from PIL import Image
        pixels = []
        for name, (_i, media_type) in book.package.manifest.items():
            if media_type.startswith("image/") and media_type != "image/svg+xml":
                with Image.open(io.BytesIO(book.body(name))) as im:
                    pixels.append(hashlib.sha256(im.convert("RGB").tobytes()).digest())
        for p in sorted(pixels):
            h.update(p)
        return h.digest()

    def mandatory_constants(self) -> list[bytes]:
        """The ZIP layer's declared fields, OCF's required `mimetype` member, and
        F1's two constants in the package document: the modified time (the ZIP
        epoch) and the identifier's `urn:uuid:` form."""
        return ZipPlugin().mandatory_constants() + [
            b"mimetype" + ocf.MIMETYPE, f1.MODIFIED.encode(),
            b"<meta property=\"dcterms:modified\">" + f1.MODIFIED.encode() + b"</meta>",
            b"urn:uuid:"]

    def guard_view(self, data: bytes) -> bytes:
        try:
            a = zipread.read(data)
        except Exception:                                 # noqa: BLE001
            return data
        parts = [data[e.local_offset:e.data_offset] for e in a.entries]
        for e in a.entries:
            if re.search(r"\.(opf|ncx|xhtml|html|xml)$", e.name):
                parts.append(e.content())
        return b"".join(parts) + data[a.cd_offset:]

    def structural_features(self, path: str) -> dict:
        book = _book(path)
        if book is None:
            return {}
        es = book.archive.entries
        # The ZIP plugin's per-name keys (`entry_order`, `methods`) would report the
        # content layout -- which files a producer writes -- through a packager-
        # shaped key, once a writer sorts. The packager's choices are policies:
        # is the order sorted, and is a member stored exactly when deflate would
        # not shrink it.
        out = {f"pkg_{k}": v for k, v in ZipPlugin().structural_features(path).items()
               if k not in ("size", "sidecars", "folder_entries", "entry_order",
                            "methods")}
        out["pkg_mimetype"] = (es[0].name == "mimetype", es[0].method, es[0].flags)
        rest = [e.name for e in es[1:]]
        out["pkg_order_sorted"] = rest == sorted(rest)
        out["pkg_method_policy"] = tuple(sorted({
            (e.method, len(zlib.compress(e.content(), 6)) - 6 < e.uncomp_size)
            for e in es[1:] if not e.is_dir}))

        opf = book.body(book.opf_path)
        els = xs.parse(opf, "OPF")
        meta = [(el.uri.rsplit("/", 2)[-2] if el.uri else "", el.local,
                 el.attr("property") or el.attr("name") or "")
                for _, el in f1._metadata_children(els)]
        out["meta_elements"] = tuple(meta)
        out["meta_package_attrs"] = tuple(sorted(els[0].attrs))
        out["meta_uid_ref"] = book.package.uid_ref
        ident = book.package.identifier or ""
        out["meta_identifier_form"] = re.sub(r"[0-9a-fA-F]", "x", ident)
        out["meta_opf_decl"] = opf[:opf.find(b"?>") + 2] if opf.startswith(b"<?xml") \
            else b""
        ncx = [n for n in book.entries if book.role(n) == "ncx"]
        out["meta_ncx_head"] = tuple(
            el.attr("name") or "" for el in xs.parse(book.body(ncx[0]), "NCX")
            if el.local == "meta") if ncx else ()

        names = sorted(n for n in book.entries if not n.endswith("/"))
        out["content_layout"] = tuple(re.sub(r"\d+", "#", n) for n in names)
        heads, decls, classes = set(), set(), set()
        for name in names:
            if book.role(name) != "xhtml":
                continue
            body = book.body(name)
            decls.add(body[:body.find(b"?>") + 2] if body.startswith(b"<?xml") else b"")
            pels = xs.parse(body, name)
            for el in pels:
                if el.parent is not None and pels[el.parent].local == "head":
                    heads.add(el.local)
                for c in (el.attr("class") or "").split():
                    classes.add(re.sub(r"\d+", "#", c))
        out["content_head"] = tuple(sorted(heads))
        out["content_decl"] = tuple(sorted(decls))
        out["content_classes"] = tuple(sorted(classes))
        css = [n for n, (_i, t) in book.package.manifest.items() if t == "text/css"]
        out["content_css_bytes"] = sum(len(book.body(n)) for n in css
                                       if n in book.entries)
        out["content_size"] = book.archive.size
        return out
