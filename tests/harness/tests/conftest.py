"""Shared fixtures: TOYF corpora, stub instances, a diverse-input set for the guard."""
from __future__ import annotations

import pytest

from tests.harness.corpus import synthetic, toy_format
from tests.harness.dispatch import ToyFormatPlugin
from tests.harness.stubs.clean_stub import CleanStub
from tests.harness.stubs.leaky_stub import LeakyStub
from tests.harness.stubs.noisy_clean_stub import NoisyCleanStub
from tests.harness.stubs.per_source_stub import PerSourceStub
from tests.harness.stubs.version_stub import VersionStub

CONTENT = b"the-quick-brown-fox-CONTENT-payload-0123456789"


@pytest.fixture
def content():
    return CONTENT


@pytest.fixture
def toy_plugin():
    return ToyFormatPlugin()


@pytest.fixture
def clean_stub():
    return CleanStub()


@pytest.fixture
def leaky_stub():
    return LeakyStub()


@pytest.fixture
def version_stub():
    return VersionStub()


@pytest.fixture
def noisy_clean_stub():
    return NoisyCleanStub()


@pytest.fixture
def per_source_stub():
    return PerSourceStub()


@pytest.fixture
def a1_variants(tmp_path):
    """Factory: make_a1_variants into this test's tmp dir."""
    def make(content: bytes = CONTENT, n_variants: int = 3, n_repeats: int = 5):
        return synthetic.make_a1_variants(content, n_variants=n_variants,
                                          n_repeats=n_repeats, tmpdir=str(tmp_path))
    return make


@pytest.fixture
def a2_corpus(tmp_path):
    """Factory: make_a2_corpus into this test's tmp dir."""
    def make(content: bytes = CONTENT, n_sources: int = 2, members_per_source: int = 3):
        return synthetic.make_a2_corpus(content, n_sources=n_sources,
                                        members_per_source=members_per_source,
                                        tmpdir=str(tmp_path))
    return make


@pytest.fixture
def toyf_file(tmp_path):
    p = str(tmp_path / "sample.toyf")
    toy_format.write(p, CONTENT, [(7, b"A" * 16), (0xA0, b"SRC0")])
    return p


@pytest.fixture
def diverse_inputs(tmp_path):
    """Distinct content (hence length) + metadata, so clean_stub introduces only
    framing and the guard sees no input-invariant signature."""
    specs = [
        (b"alpha", [(1, b"x")]),
        (b"beta-content-2", [(2, b"yy"), (7, b"AAAA")]),
        (b"gamma content number three", [(0xA0, b"SRC9")]),
        (b"d", []),
        (b"epsilon-payload-much-longer-than-the-rest-here", [(9, b"zzzz")]),
    ]
    paths = []
    for i, (c, recs) in enumerate(specs):
        p = str(tmp_path / f"diverse_{i}.toyf")
        toy_format.write(p, c, recs)
        paths.append(p)
    return paths
