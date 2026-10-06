"""HEIC corpus for Phase 4.

Two sources, and the split matters (see `docs/p4_media_plan.md` §W5):

- **Synthetic**, via `pillow-heif`. Runs everywhere including CI, carries the
  metadata items F1 must remove, and gives an **independent decoder** for checking
  that the pixels survived — which is exactly where an implementation that shares no
  code with ours belongs.
- **Real iPhone photos**, in a git-ignored directory. They are what a synthetic file
  cannot be: a 61-to-95-tile grid, six auxiliary images, a thumbnail, and Apple's
  58 KB semantic-segmentation plist. Absent on CI, and reported absent rather than
  quietly skipped — the limit-#12 precedent.

The scrubber itself depends on neither: we parse ISOBMFF ourselves. `pillow-heif` is
test-side only, for building inputs and for verifying output we did not build.
"""
from __future__ import annotations

import glob
import io
import os

try:
    import pillow_heif
    from PIL import Image
    pillow_heif.register_heif_opener()
    HAVE_HEIF = True
except ImportError:                                       # pragma: no cover
    HAVE_HEIF = False

# Real photos: git-ignored (`*.HEIC` in .gitignore), never committed, and carrying
# genuine GPS. Point this elsewhere with the env var if you keep them somewhere else.
REAL_DIR = os.environ.get(
    "HEIC_REAL_SAMPLES",
    os.path.join(os.path.dirname(__file__), "..", "..", "metadata-research", "step2"))

SENTINEL = "HEIC-SENTINEL"


def real_samples() -> list[str]:
    """Real camera HEICs on this machine, or []. Never bundled."""
    if os.environ.get("SCRUB_IGNORE_REAL_SAMPLES"):
        return []                       # the published test count (test_readme_claims)
    if not os.path.isdir(REAL_DIR):
        return []
    out: list[str] = []
    for pattern in ("*.HEIC", "*.heic"):
        out.extend(glob.glob(os.path.join(REAL_DIR, pattern)))
    return sorted(out)


HAVE_REAL = bool(real_samples())


def _exif_payload(variant: str = "") -> bytes:
    import piexif
    return piexif.dump({
        "0th": {
            piexif.ImageIFD.Make: b"TestCam",
            piexif.ImageIFD.Model: b"MZ-1",
            piexif.ImageIFD.Software: f"{SENTINEL}-app 1.0{variant}".encode(),
            piexif.ImageIFD.Artist: f"{SENTINEL}-author{variant}".encode(),
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: b"2020:01:01 12:00:00",
        },
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((51, 1), (30, 1), (0, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b"W",
            piexif.GPSIFD.GPSLongitude: ((0, 1), (7, 1), (0, 1)),
        },
    })


def _xmp_payload(variant: str = "") -> bytes:
    return (f'<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 6.0.0">'
            f'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            f'<rdf:Description><creator>{SENTINEL}-xmp-author{variant}</creator>'
            f'</rdf:Description></rdf:RDF></x:xmpmeta>').encode()


def _pixels(width: int = 64, height: int = 48, seed: int = 0):
    """A deterministic, non-flat image. Flat colour compresses to almost nothing and
    would make a pixel-identity check pass for the wrong reason."""
    img = Image.new("RGB", (width, height))
    img.putdata([((x * 7 + seed) % 256, (y * 11 + seed) % 256, (x * y + seed) % 256)
                 for y in range(height) for x in range(width)])
    return img


def synthetic(path: str, *, with_exif: bool = True, with_xmp: bool = True,
              seed: int = 0, quality: int = 80) -> str:
    """A small HEIC carrying the metadata items F1 has to remove."""
    if not HAVE_HEIF:
        raise RuntimeError("pillow-heif is required to build a synthetic HEIC")
    kwargs: dict = {"quality": quality}
    if with_exif:
        kwargs["exif"] = _exif_payload()
    if with_xmp:
        kwargs["xmp"] = _xmp_payload()
    _pixels(seed=seed).save(path, format="HEIF", **kwargs)
    return path


def torture(path: str) -> str:
    """Every metadata locus a synthetic file can carry at once."""
    return synthetic(path, with_exif=True, with_xmp=True)


def clean(path: str, seed: int = 0) -> str:
    """The control: same pixels, no metadata items. What a scrubbed file should look
    like structurally, built independently of our scrubber."""
    return synthetic(path, with_exif=False, with_xmp=False, seed=seed)


def decoded_pixels(path_or_bytes) -> bytes:
    """The image as an independent decoder sees it — the content-identity oracle.

    `pillow-heif` shares no code with our walker, so a pixel match here is evidence
    rather than us agreeing with ourselves. Returns b"" when the decoder is absent,
    so a caller reports *not measured* instead of *clean*.
    """
    if not HAVE_HEIF:
        return b""
    try:
        source = (io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes)
                  else path_or_bytes)
        with Image.open(source) as im:
            return im.convert("RGB").tobytes()
    except Exception:                                     # noqa: BLE001
        return b""


def available() -> dict[str, bool]:
    """What this machine can do, so a caller can report the gap rather than shrink
    the claim silently."""
    return {"synthetic": HAVE_HEIF, "real_camera_photos": HAVE_REAL,
            "decoder": HAVE_HEIF}


# --------------------------------------------------------------------------- #
# The hand-built HEIC — M5
# --------------------------------------------------------------------------- #
# A `pillow-heif` file is one tile, no grid, no auxiliary image, no thumbnail and
# no `idat`. Every finding that made HEIC interesting lives in the paths it cannot
# express, so CI was regression-testing the easy half and thirteen tests skipped on
# exactly the hard one.
#
# So this half of the corpus writes the container **byte by byte**, and deliberately
# imports nothing from `src.scrub`: the box writer below is a second implementation,
# and a shared misunderstanding of `iloc` cannot cancel itself out. `pillow-heif` is
# still used, but only as an HEVC *encoder* — the tile bitstreams are real, which is
# what lets the acceptance test decode the result rather than merely parse it.
#
# What it carries, and why each one is here:
#
#   a 2x2 GRID primary          the shape of every iPhone photo; `iloc` construction
#                               method 1, bytes in `idat`, not at a file offset
#   a 2x1 gain-map GRID (aux)   a TILED auxiliary image: dropping it must drop its
#                               tiles too, which is the leak M5 found
#   a 1x2 matte GRID (aux)      a tiled aux that IS dropped — the other side of it
#   a single-tile matte (aux)   the untiled case
#   a `thmb` thumbnail          Phase 1's lesson in a container
#   `Exif` / `mime` / `uri `    the metadata items themselves
#   an `altr` entity group      a fourth table that names items by id
#   shared `ipco` properties    so pruning forces the renumbering
#
# The three grids are deliberately DIFFERENT SHAPES (2x2, 2x1, 1x2). They were the
# same shape first, and that made the file useless for catching the `idat` offset
# bug: every grid descriptor was byte-identical, so writing them all at offset 0
# still decoded correctly.

_GRID_PRIMARY, _GRID_GAIN, _GRID_MATTE = 5, 8, 11
_ITEM_MATTE_FLAT, _ITEM_THUMB = 12, 13
_ITEM_EXIF, _ITEM_XMP, _ITEM_URI = 14, 15, 16

AUX_GAIN = "urn:com:apple:photo:2020:aux:hdrgainmap"
AUX_MATTE_TILED = "urn:com:apple:photo:2019:aux:semanticskinmatte"
AUX_MATTE_FLAT = "tag:apple.com,2023:photo:aux:linearthumbnail"


def _box(kind: bytes, body: bytes) -> bytes:
    return (len(body) + 8).to_bytes(4, "big") + kind + body


def _full(kind: bytes, version: int, flags: int, body: bytes) -> bytes:
    return _box(kind, bytes([version]) + flags.to_bytes(3, "big") + body)


def _grid_descriptor(rows: int, cols: int, width: int, height: int) -> bytes:
    """ISO/IEC 23008-12 `ImageGrid`: version, flags, rows-1, cols-1, w, h."""
    return (bytes([0, 0, rows - 1, cols - 1])
            + width.to_bytes(2, "big") + height.to_bytes(2, "big"))


def _read_boxes(data: bytes, start: int = 0, end: int | None = None):
    """A second, deliberately tiny ISOBMFF reader. Used to lift the encoded tile out
    of a `pillow-heif` file; shares no code with the scrubber's walker."""
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        if size == 0:
            size = end - pos
        if size < 8 or pos + size > end:
            break
        yield kind, data[pos + 8:pos + size]
        pos += size


def _encoded_tile(width: int, height: int, *, seed: int, quality: int):
    """One real HEVC tile plus the `hvcC` that decodes it, from `pillow-heif`.

    The container around them is ours; the bitstream is not. That split is what makes
    a decode test on a hand-built file meaningful.
    """
    if not HAVE_HEIF:
        raise RuntimeError("pillow-heif is required to build HEIC tiles")
    buf = io.BytesIO()
    _pixels(width, height, seed=seed).save(buf, format="HEIF", quality=quality)
    blob = buf.getvalue()

    meta = next(body for kind, body in _read_boxes(blob) if kind == b"meta")
    children = dict(_read_boxes(meta[4:]))          # skip meta's version+flags

    # `iloc` — one item, so the first entry is the tile. Widths are read rather
    # than assumed; pillow-heif writes version 0 with a 4-byte base offset.
    iloc = children[b"iloc"]
    offset_size, length_size = iloc[4] >> 4, iloc[4] & 0xF
    base_size = iloc[5] >> 4
    i = 8 + 2 + 2 + base_size            # item_id, data_ref_index, base_offset
    base = int.from_bytes(iloc[8 + 2 + 2:8 + 2 + 2 + base_size], "big")
    i += 2                                # extent_count
    tile_offset = base + int.from_bytes(iloc[i:i + offset_size], "big")
    i += offset_size
    tile_length = int.from_bytes(iloc[i:i + length_size], "big")

    ipco = next(body for kind, body in _read_boxes(children[b"iprp"])
                if kind == b"ipco")
    props = dict(_read_boxes(ipco))
    return blob[tile_offset:tile_offset + tile_length], props[b"hvcC"], props.get(b"colr")


# Container-writer profiles, for the A2 peer set. Every one of these is a choice the
# writing software makes and the picture does not: which brand it stamps, the order
# it lays `meta`'s tables out in, how wide it makes `iloc`'s fields, and which
# optional boxes it bothers with. The values are drawn from two real writers —
# `apple` is what the six iPhone photos measure as, `libheif` is what `pillow-heif`
# emits — so the peer set is a stand-in for different producers rather than an
# invention. Named as such in the matrix, the way E-FLAC names compression levels.
PRODUCERS = {
    "apple": {
        "brand": b"heic",
        "compatible": (b"mif1", b"MiHB", b"heic", b"miaf"),
        "order": (b"hdlr", b"dinf", b"pitm", b"iinf", b"iref", b"iprp", b"grpl",
                  b"idat", b"iloc"),
        "iloc": (1, 4, 4, 0),
    },
    "generic": {
        "brand": b"mif1",
        "compatible": (b"mif1", b"heic"),
        "order": (b"hdlr", b"pitm", b"iloc", b"iinf", b"iref", b"iprp", b"idat"),
        "iloc": (1, 4, 4, 4),
    },
    "compact": {
        "brand": b"heix",
        "compatible": (b"mif1", b"heix", b"heic"),
        "order": (b"hdlr", b"iloc", b"iinf", b"pitm", b"iprp", b"iref", b"idat"),
        "iloc": (1, 4, 8, 0),
    },
}


def handbuilt(path: str, *, with_exif: bool = True, with_xmp: bool = True,
              with_uri: bool = True, with_thumbnail: bool = True,
              with_aux: bool = True, with_group: bool = True,
              tile: int = 64, seed: int = 0, quality: int = 70,
              producer: str = "apple", variant: str = "") -> str:
    """Write a HEIC with every structure a real iPhone photo has.

    `producer` picks a container-writer profile (see `PRODUCERS`); `variant` is a
    string stamped into the metadata only, so two variants differ in what a scrub
    must remove and in nothing else.
    """
    profile = PRODUCERS[producer]
    tile_bytes, hvcc, colr = _encoded_tile(tile, tile, seed=seed, quality=quality)

    # (item_id, type, payload) for everything stored in `mdat`, in write order.
    mdat_items: list[tuple[int, bytes, bytes]] = []
    for n in range(4):                                       # primary grid tiles
        mdat_items.append((1 + n, b"hvc1", tile_bytes))
    if with_aux:
        for n in range(2):                                   # gain-map tiles
            mdat_items.append((6 + n, b"hvc1", tile_bytes))
        for n in range(2):                                   # matte tiles
            mdat_items.append((9 + n, b"hvc1", tile_bytes))
        mdat_items.append((_ITEM_MATTE_FLAT, b"hvc1", tile_bytes))
    if with_thumbnail:
        mdat_items.append((_ITEM_THUMB, b"hvc1", tile_bytes))
    if with_exif:
        # An `Exif` item begins with a 4-byte offset to the TIFF header.
        mdat_items.append((_ITEM_EXIF, b"Exif",
                           b"\x00\x00\x00\x00" + _exif_payload(variant)))
    if with_xmp:
        mdat_items.append((_ITEM_XMP, b"mime", _xmp_payload(variant)))
    if with_uri:
        mdat_items.append((_ITEM_URI, b"uri ", _segmentation_plist(variant)))

    grids: list[tuple[int, bytes]] = [
        (_GRID_PRIMARY, _grid_descriptor(2, 2, tile * 2, tile * 2))]
    if with_aux:
        grids.append((_GRID_GAIN, _grid_descriptor(1, 2, tile * 2, tile)))
        grids.append((_GRID_MATTE, _grid_descriptor(2, 1, tile, tile * 2)))

    idat = b"".join(body for _iid, body in grids)
    idat_spans, cursor = {}, 0
    for item_id, body in grids:
        idat_spans[item_id] = (cursor, len(body))
        cursor += len(body)

    # ---- meta's tables ---------------------------------------------------- #
    all_items = [(iid, kind) for iid, kind, _b in mdat_items] \
        + [(iid, b"grid") for iid, _b in grids]
    infes = b"".join(
        _full(b"infe", 2, 0, iid.to_bytes(2, "big") + b"\x00\x00" + kind + b"\x00")
        for iid, kind in sorted(all_items))
    iinf = _full(b"iinf", 0, 0, len(all_items).to_bytes(2, "big") + infes)

    refs: list[tuple[bytes, int, list[int]]] = [(b"dimg", _GRID_PRIMARY, [1, 2, 3, 4])]
    if with_aux:
        refs += [(b"dimg", _GRID_GAIN, [6, 7]),
                 (b"auxl", _GRID_GAIN, [_GRID_PRIMARY]),
                 (b"dimg", _GRID_MATTE, [9, 10]),
                 (b"auxl", _GRID_MATTE, [_GRID_PRIMARY]),
                 (b"auxl", _ITEM_MATTE_FLAT, [_GRID_PRIMARY])]
    if with_thumbnail:
        refs.append((b"thmb", _ITEM_THUMB, [_GRID_PRIMARY]))
    for iid, present in ((_ITEM_EXIF, with_exif), (_ITEM_XMP, with_xmp),
                         (_ITEM_URI, with_uri)):
        if present:
            refs.append((b"cdsc", iid, [_GRID_PRIMARY]))
    iref = _full(b"iref", 0, 0, b"".join(
        _box(kind, frm.to_bytes(2, "big") + len(to).to_bytes(2, "big")
             + b"".join(t.to_bytes(2, "big") for t in to))
        for kind, frm, to in refs))

    # ---- `ipco` / `ipma`: properties shared BY INDEX ---------------------- #
    props = [_box(b"hvcC", hvcc),
             _box(b"ispe", b"\x00\x00\x00\x00" + tile.to_bytes(4, "big") * 2),
             _box(b"ispe", b"\x00\x00\x00\x00"
                  + (tile * 2).to_bytes(4, "big") * 2),
             _box(b"ispe", b"\x00\x00\x00\x00" + (tile * 2).to_bytes(4, "big")
                  + tile.to_bytes(4, "big")),
             _box(b"ispe", b"\x00\x00\x00\x00" + tile.to_bytes(4, "big")
                  + (tile * 2).to_bytes(4, "big")),
             _full(b"auxC", 0, 0, AUX_GAIN.encode() + b"\x00"),
             _full(b"auxC", 0, 0, AUX_MATTE_TILED.encode() + b"\x00"),
             _full(b"auxC", 0, 0, AUX_MATTE_FLAT.encode() + b"\x00")]
    if colr:
        props.append(_box(b"colr", colr))
    HVCC, ISPE_TILE, ISPE_FULL, ISPE_WIDE, ISPE_TALL = 1, 2, 3, 4, 5
    AUXC_GAIN, AUXC_MATTE, AUXC_FLAT = 6, 7, 8
    COLR = 9 if colr else 0

    assoc: dict[int, list[int]] = {}
    for iid, kind in all_items:
        if kind == b"hvc1":
            assoc[iid] = [HVCC, ISPE_TILE]
    assoc[_GRID_PRIMARY] = [ISPE_FULL] + ([COLR] if COLR else [])
    if with_aux:
        assoc[_GRID_GAIN] = [ISPE_WIDE, AUXC_GAIN]
        assoc[_GRID_MATTE] = [ISPE_TALL, AUXC_MATTE]
        assoc[_ITEM_MATTE_FLAT] = [HVCC, ISPE_TILE, AUXC_FLAT]
    ipma_body = len(assoc).to_bytes(4, "big") + b"".join(
        iid.to_bytes(2, "big") + bytes([len(idx)]) + bytes(idx)
        for iid, idx in sorted(assoc.items()))
    iprp = _box(b"iprp", _box(b"ipco", b"".join(props))
                + _full(b"ipma", 0, 0, ipma_body))

    grpl = b""
    if with_group and b"grpl" in profile["order"]:
        # An `altr` group naming an item that F1 removes — a fourth table that can
        # be left pointing at nothing.
        entities = [_GRID_PRIMARY] + ([_GRID_MATTE] if with_aux else [])
        grpl = _box(b"grpl", _full(
            b"altr", 0, 0,
            (900).to_bytes(4, "big") + len(entities).to_bytes(4, "big")
            + b"".join(e.to_bytes(4, "big") for e in entities)))

    version, offset_size, length_size, base_size = profile["iloc"]

    def _iloc(file_offsets: dict[int, tuple[int, int]]) -> bytes:
        """Construction method 1 for the grids (bytes in `idat`), method 0 for
        everything else. Field widths come from the producer profile — they are not
        constant across writers, which is why rewriting `iloc` cannot use a fixed
        struct format."""
        entries = []
        for item_id, _kind in sorted(all_items):
            if item_id in idat_spans:
                method, (offset, length) = 1, idat_spans[item_id]
            else:
                method, (offset, length) = 0, file_offsets[item_id]
            entries.append(
                item_id.to_bytes(2, "big") + method.to_bytes(2, "big")
                + b"\x00\x00" + b"\x00" * base_size + (1).to_bytes(2, "big")
                + offset.to_bytes(offset_size, "big")
                + length.to_bytes(length_size, "big"))
        return _full(b"iloc", version, 0,
                     bytes([(offset_size << 4) | length_size, base_size << 4])
                     + len(entries).to_bytes(2, "big") + b"".join(entries))

    hdlr = _full(b"hdlr", 0, 0, b"\x00" * 4 + b"pict" + b"\x00" * 12 + b"\x00")
    dinf = _box(b"dinf", _full(b"dref", 0, 0,
                               (1).to_bytes(4, "big") + _full(b"url ", 0, 1, b"")))
    pitm = _full(b"pitm", 0, 0, _GRID_PRIMARY.to_bytes(2, "big"))

    def _meta(iloc_box: bytes) -> bytes:
        parts = {b"hdlr": hdlr, b"dinf": dinf, b"pitm": pitm, b"iinf": iinf,
                 b"iref": iref, b"iprp": iprp, b"grpl": grpl,
                 b"idat": _box(b"idat", idat), b"iloc": iloc_box}
        return _full(b"meta", 0, 0,
                     b"".join(parts[kind] for kind in profile["order"]))

    ftyp = _box(b"ftyp", profile["brand"] + (0).to_bytes(4, "big")
                + b"".join(profile["compatible"]))

    # Two passes, for our own reason: `iloc` entry sizes are fixed, so meta's size is
    # known before the absolute offsets are.
    placeholder = {iid: (0, len(body)) for iid, _k, body in mdat_items}
    mdat_start = len(ftyp) + len(_meta(_iloc(placeholder))) + 8
    offsets, cursor = {}, mdat_start
    for iid, _kind, body in mdat_items:
        offsets[iid] = (cursor, len(body))
        cursor += len(body)
    blob = ftyp + _meta(_iloc(offsets)) \
        + _box(b"mdat", b"".join(body for _i, _k, body in mdat_items))

    with open(path, "wb") as handle:
        handle.write(blob)
    return path


def _segmentation_plist(variant: str = "") -> bytes:
    """A binary plist shaped like Apple's `uri ` item: a machine-made claim about
    the subject, not a fact about the camera."""
    import plistlib
    return plistlib.dumps(
        {"segmentation": {"PeopleRatio": 0.42, "SkinRatio": 0.0031,
                          "note": SENTINEL + "-subject" + variant}},
        fmt=plistlib.FMT_BINARY)
