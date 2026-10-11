"""OCF, the container an EPUB is: which file is the book, and how fonts are hidden.

Three facts from the EPUB specifications this module turns into code:

- `META-INF/container.xml` names the package document (the OPF), the one file that
  says what the book is made of. More than one ("multiple renditions") is refused.
- **Font obfuscation is keyed by the book's identifier.** A publisher who embeds a
  font may scramble its first bytes so it cannot be lifted from the book: the IDPF
  algorithm XORs the first 1040 bytes with the SHA-1 of the unique identifier
  (whitespace removed), Adobe's XORs the first 1024 with the identifier's UUID.
  F1 replaces the identifier (it is a random per-export ID that links copies to one
  export), so every obfuscated font is unscrambled with the old key and scrambled
  again with the new one -- or the fonts silently turn to garbage.
- **Anything else in `encryption.xml` is DRM**, and `rights.xml`/`signatures.xml`
  are licences and signatures a changed book cannot keep: refused.

The new identifier is derived from the cleaned book, not drawn at random (Phase 5's
rule for build IDs): the same content cleaned twice gets the same identifier, and
the identifier says nothing the content does not.
"""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET

from ...errors import ParseError

MIMETYPE = b"application/epub+zip"
CONTAINER = "META-INF/container.xml"
ENCRYPTION = "META-INF/encryption.xml"
REFUSED = {"META-INF/rights.xml": "a rights (DRM) record",
           "META-INF/signatures.xml": "digital signatures, which a changed book "
                                      "cannot keep valid"}
KEPT_META_INF = {CONTAINER, ENCRYPTION, "META-INF/com.apple.ibooks.display-options.xml"}

IDPF = "http://www.idpf.org/2008/embedding"
ADOBE = "http://ns.adobe.com/pdf/enc#RC"
_ENC = "{http://www.w3.org/2001/04/xmlenc#}"
_CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"

FONT_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"wOFF", b"wOF2", b"ttcf")
PLACEHOLDER = "urn:uuid:00000000-0000-8000-8000-000000000000"


def rootfile(container: bytes) -> str:
    try:
        root = ET.fromstring(container)
    except ET.ParseError as exc:
        raise ParseError(f"EPUB: {CONTAINER} is not well-formed ({exc})") from None
    paths = [r.get("full-path") for r in root.iter(f"{_CONTAINER_NS}rootfile")
             if r.get("media-type") == "application/oebps-package+xml"]
    if not paths or not paths[0]:
        raise ParseError(f"EPUB: {CONTAINER} names no package document")
    if len(paths) > 1:
        raise ParseError("EPUB: more than one rendition (package document); refused")
    return paths[0]


def obfuscated(encryption: bytes) -> dict[str, str]:
    """{path in the archive: algorithm} for every obfuscated font. Anything
    encrypted another way is DRM, and refused."""
    try:
        root = ET.fromstring(encryption)
    except ET.ParseError as exc:
        raise ParseError(f"EPUB: {ENCRYPTION} is not well-formed ({exc})") from None
    out = {}
    for data in root.iter(f"{_ENC}EncryptedData"):
        method = data.find(f"{_ENC}EncryptionMethod")
        ref = data.find(f"{_ENC}CipherData/{_ENC}CipherReference")
        algo = method.get("Algorithm") if method is not None else None
        uri = ref.get("URI") if ref is not None else None
        if algo not in (IDPF, ADOBE) or not uri:
            raise ParseError("EPUB: encrypted content (DRM); refused -- only font "
                             "obfuscation can be carried through a clean")
        out[uri] = algo
    if root.find(f".//{_ENC}EncryptedKey") is not None:
        raise ParseError("EPUB: an encrypted key (DRM); refused")
    return out


def key(identifier: str, algorithm: str) -> bytes:
    if algorithm == IDPF:
        return hashlib.sha1(re.sub(r"[ \t\r\n]", "", identifier).encode()).digest()
    hexdigits = re.sub(r"(?i)^urn:uuid:|-", "", identifier.strip())
    if not re.fullmatch(r"[0-9a-fA-F]{32}", hexdigits):
        raise ParseError("EPUB: a font obfuscated with Adobe's algorithm needs a "
                         "UUID identifier, and this book's is not one")
    return bytes.fromhex(hexdigits)


def xor(font: bytes, k: bytes, algorithm: str) -> bytes:
    """Obfuscate or unobfuscate (the operation is its own inverse)."""
    n = min(len(font), 1040 if algorithm == IDPF else 1024)
    head = bytes(b ^ k[i % len(k)] for i, b in enumerate(font[:n]))
    return head + font[n:]


def is_font(data: bytes) -> bool:
    return data[:4] in FONT_MAGIC


def derive_identifier(members: dict[str, bytes]) -> str:
    """A UUID (version 8, RFC 9562's custom form) from SHA-256 over the cleaned
    book: every member's name and bytes, in name order."""
    h = hashlib.sha256()
    for name in sorted(members):
        h.update(name.encode() + b"\0" + hashlib.sha256(members[name]).digest())
    b = bytearray(h.digest()[:16])
    b[6] = (b[6] & 0x0F) | 0x80
    b[8] = (b[8] & 0x3F) | 0x80
    x = b.hex()
    return f"urn:uuid:{x[:8]}-{x[8:12]}-{x[12:16]}-{x[16:20]}-{x[20:]}"
