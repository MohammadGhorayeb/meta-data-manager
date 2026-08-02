from ..contract import ScrubResult
from ..corpus.toy_format import pack, read


class CleanStub:
    """REQUIRED. Canonical: strips all metadata. Deterministic => empty floor =>
    content-identical pair yields byte-identical outputs => A1 pass."""
    name, version = "clean_stub", "0.1"

    def run(self, in_path, out_path, fidelity):
        content, _ = read(in_path)
        with open(out_path, "wb") as f: f.write(pack(content, []))
        return ScrubResult(ok=True, out_path=out_path, fidelity=fidelity)
