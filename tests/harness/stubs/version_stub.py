from ..corpus.toy_format import read, pack
from ..contract import ScrubResult

VERSION_TAG = b"\x00TOYSCRUB/1.2.3"


class VersionStub:
    """REQUIRED. Clean re. content/metadata, but stamps a constant tool signature
    on every output. The A1 per-pair oracle PASSES (signature is variant-invariant);
    the fingerprint guard FAILS it."""
    name, version = "version_stub", "1.2.3"

    def run(self, in_path, out_path, fidelity):
        content, _ = read(in_path)
        with open(out_path, "wb") as f: f.write(pack(content, []) + VERSION_TAG)
        return ScrubResult(ok=True, out_path=out_path, fidelity=fidelity)
