from ..corpus.toy_format import read, pack
from ..contract import ScrubResult


class PerSourceStub:
    """OPTIONAL (exercises A2). Preserves a per-source structural feature (record
    type 0xA0) while dropping incidental metadata => surviving per-source-constant,
    across-source-variable feature = source fingerprint."""
    name, version = "per_source_stub", "0.1"

    def run(self, in_path, out_path, fidelity):
        content, records = read(in_path)
        kept = [(t, v) for (t, v) in records if t == 0xA0]   # source-marking record survives
        with open(out_path, "wb") as f: f.write(pack(content, kept))
        return ScrubResult(ok=True, out_path=out_path, fidelity=fidelity)
