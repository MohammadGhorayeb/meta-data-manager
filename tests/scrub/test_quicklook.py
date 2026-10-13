"""Content checked by Apple's own renderer (`docs/p6_tail_plan.md` §14).

The pixel tests decode with Pillow, which ignores how a picture is DISPLAYED: the
sideways-photo bug (EXIF rotation dropped by every JPEG tier) passed them all. Quick
Look is what Finder and Preview draw with -- rotation, colour profile and
transparency applied -- so each picture is rendered before and after F1 and the two
renders must be the same pixels. macOS only.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import sys

import pytest

from src.scrub.dispatch import default_dispatcher
from tests.scrub import gif_corpus as gfc
from tests.scrub import heic_corpus as hc
from tests.scrub import tiff_corpus as tfc
from tests.scrub import webp_corpus as wbc
from tests.scrub import zip_corpus as zc

pytestmark = pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("qlmanage"),
                                reason="Quick Look is macOS's renderer")


def _portrait_jpeg(orientation: bool = True) -> bytes:
    """Stored landscape with Orientation 6, as a phone held upright writes it."""
    import piexif
    from PIL import Image
    img = Image.new("RGB", (64, 32), (20, 120, 200))
    img.paste((220, 30, 30), (0, 0, 16, 32))
    tags = {piexif.ImageIFD.Artist: b"SENTINEL-ARTIST"}
    if orientation:
        tags[piexif.ImageIFD.Orientation] = 6
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92, exif=piexif.dump({"0th": tags}))
    return buf.getvalue()


def _alpha_heic() -> bytes:
    from PIL import Image
    img = Image.new("RGBA", (48, 48), (200, 40, 40, 255))
    img.paste((0, 0, 0, 0), (0, 0, 24, 48))
    buf = io.BytesIO()
    img.save(buf, format="HEIF", quality=90)
    return buf.getvalue()


FILES = {"portrait.jpg": _portrait_jpeg, "photo.jpg": lambda: zc.photo(1),
         "icon.png": lambda: zc.png(1), "scan.tif": tfc.build,
         "anim.gif": lambda: gfc.build(frames=3), "pic.webp": wbc.build,
         "alpha.heic": _alpha_heic}


def _render(tmp_path, name: str, data: bytes, tag: str) -> bytes:
    from PIL import Image
    src = tmp_path / tag / name
    src.parent.mkdir(exist_ok=True)
    src.write_bytes(data)
    out = tmp_path / f"ql_{tag}"
    out.mkdir(exist_ok=True)
    subprocess.run(["qlmanage", "-t", "-s", "128", "-o", str(out), str(src)],
                   capture_output=True, timeout=60)
    png = out / f"{name}.png"
    assert png.exists(), f"Quick Look rendered nothing for {name}"
    with Image.open(png) as im:
        return im.size, im.convert("RGBA").tobytes()


@pytest.mark.parametrize("name", sorted(FILES))
def test_quick_look_draws_the_same_picture_after(name, tmp_path):
    if name.endswith(".heic") and not hc.HAVE_HEIF:
        pytest.skip("pillow-heif not installed")
    data = FILES[name]()
    clean = default_dispatcher().resolve(data).scrub(data, "F1")
    assert _render(tmp_path, name, data, "in") == _render(tmp_path, name, clean, "out")


def test_quick_look_applies_rotation_so_the_check_can_fail(tmp_path):
    """Non-vacuity: the same photo without its rotation tag renders differently.
    Had Quick Look ignored the tag, the check above could not have caught the
    sideways-photo bug."""
    upright = _render(tmp_path, "a.jpg", _portrait_jpeg(True), "a")
    sideways = _render(tmp_path, "b.jpg", _portrait_jpeg(False), "b")
    assert upright[0] == (32, 64) and sideways[0] == (64, 32)
