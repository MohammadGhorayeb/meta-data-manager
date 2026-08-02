from ..contract import ScrubResult
from ..corpus.toy_format import pack, read


class LeakyStub:
    """REQUIRED. Fails to strip the first metadata record. Deterministic => for a
    content-identical pair whose first record VALUE differs, outputs differ in
    that value => variant-correlated A1 leak."""
    name, version = "leaky_stub", "0.1"

    def run(self, in_path, out_path, fidelity):
        content, records = read(in_path)
        kept = records[:1]                      # BUG-by-design: one record survives
        with open(out_path, "wb") as f: f.write(pack(content, kept))
        return ScrubResult(ok=True, out_path=out_path, fidelity=fidelity)
