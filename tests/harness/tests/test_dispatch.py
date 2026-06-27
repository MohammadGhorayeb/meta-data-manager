"""Unit tests for dispatch.py (spec §9.9).

A TOYF header resolves to ToyFormatPlugin; any other header falls through to
GenericPlugin. Also pins ToyFormatPlugin's mandatory constant and its
content-region canonicalization.
"""
from __future__ import annotations

import os

from tests.harness.corpus import toy_format
from tests.harness.dispatch import Dispatcher, GenericPlugin, ToyFormatPlugin


def test_toyf_header_resolves_to_toy_plugin(tmp_path, content):
    p = str(tmp_path / "real.toyf")
    toy_format.write(p, content, [(7, b"A" * 16)])

    disp = Dispatcher()
    plugin = ToyFormatPlugin()
    disp.register(plugin)

    resolved = disp.resolve(p)
    assert resolved is plugin
    assert isinstance(resolved, ToyFormatPlugin)
    assert resolved.format_id == "toyf"


def test_non_toyf_header_resolves_to_generic(tmp_path):
    p = str(tmp_path / "not_toyf.bin")
    # a header that deliberately is not the TOYF magic.
    payload = b"XYZW" + os.urandom(64)
    assert payload[:4] != toy_format.MAGIC
    with open(p, "wb") as f:
        f.write(payload)

    disp = Dispatcher()
    disp.register(ToyFormatPlugin())

    resolved = disp.resolve(p)
    assert isinstance(resolved, GenericPlugin)
    assert resolved.format_id == "generic"


def test_toy_plugin_mandatory_constants():
    assert ToyFormatPlugin().mandatory_constants() == [b"TOYF"]


def test_toy_plugin_canonical_content_returns_content_region(tmp_path, content):
    p = str(tmp_path / "sample.toyf")
    toy_format.write(p, content, [(7, b"A" * 16), (0xA0, b"SRC0")])
    plugin = ToyFormatPlugin()
    # canonical content is the content region only -- metadata is excluded.
    assert plugin.canonical_content(p) == content
