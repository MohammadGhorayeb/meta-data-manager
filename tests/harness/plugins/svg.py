"""SvgPlugin — harness-side format knowledge for SVG.

Content identity is the rendering: `rsvg-convert` (librsvg) rasterises the drawing
and the pixels are hashed; b"" when it is absent, so a caller reports *not
measured*. `structural_features` is the A2 channel: how a producer writes XML --
its declaration and DOCTYPE, the namespaces it declares, the root's attributes,
its element and attribute vocabulary, its whitespace habit, and the size.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import xml.parsers.expat


class SvgPlugin:
    format_id = "svg"

    def matches(self, header: bytes, path: str = "") -> bool:
        return header.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<")

    def annotate(self, in_path: str, offset: int) -> str | None:
        data = open(in_path, "rb").read()
        start = data.rfind(b"<", 0, offset + 1)
        m = re.match(rb"<([^\s/>]+)", data[start:]) if start >= 0 else None
        return m.group(1).decode("latin-1") if m else None

    def canonical_content(self, path: str) -> bytes:
        if shutil.which("rsvg-convert") is None:
            return b""
        with tempfile.TemporaryDirectory() as td:
            png = os.path.join(td, "r.png")
            r = subprocess.run(["rsvg-convert", "-w", "160", path, "-o", png],
                               capture_output=True)
            if r.returncode:
                return b""
            from PIL import Image
            return hashlib.sha256(Image.open(png).convert("RGBA").tobytes()).digest()

    def mandatory_constants(self) -> list[bytes]:
        """The newline a removed comment, instruction or element leaves INSIDE the
        root, where the whitespace between elements is text a parser reports (the
        prolog is tidied). A single fill character: the guard strips it from run
        edges and cuts at runs of it, so any real string beside it still counts."""
        return [b"\n"]

    def guard_view(self, data: bytes) -> bytes:
        """The SVG layer for the fingerprint guard: each embedded resource's payload
        blanked. Base64 shifts every byte after a removed segment into different
        text, so comparing payloads as text compares alignments, not content; the
        pictures themselves are guarded in the JPEG/PNG/GIF/WebP matrices."""
        return re.sub(rb"(data:[^,]*,)[^\"')<]*", rb"\1", data)

    def structural_features(self, path: str) -> dict:
        data = open(path, "rb").read()
        if data[:2] == b"\x1f\x8b":
            import gzip
            data = gzip.decompress(data)
        elements, attrs, nss, root = [], set(), [], {}

        def start(name, a):
            if not elements:
                root.update(a)
            elements.append(name.rpartition(" ")[2])
            attrs.update(n.rpartition(" ")[2] for n in a)
        p = xml.parsers.expat.ParserCreate(namespace_separator=" ")
        p.StartElementHandler = start
        p.StartNamespaceDeclHandler = lambda prefix, uri: nss.append(uri)
        try:
            p.Parse(data, True)
        except xml.parsers.expat.ExpatError:
            return {}
        return {"declaration": data[:data.find(b"?>") + 2] if data.startswith(b"<?xml")
                else b"", "doctype": b"<!DOCTYPE" in data[:2048],
                "namespaces": tuple(sorted(set(nss))),
                "root_attributes": tuple(sorted(n.rpartition(" ")[2] for n in root)),
                "elements": tuple(sorted(set(elements))),
                "attributes": tuple(sorted(attrs)),
                "newline_style": data.count(b"\n") > len(elements) // 2,
                "size": len(data)}
