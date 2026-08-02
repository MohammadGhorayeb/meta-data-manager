import os

from ..contract import ScrubResult
from ..corpus.toy_format import pack, read


class NoisyCleanStub:
    """OPTIONAL (exercises non-empty floor / the F3 . non-det-F2 path). Canonical
    content + a per-run nonce. Nonce varies across repeats => it is FLOOR, not a
    leak; the harness must NOT flag it."""
    name, version = "noisy_clean_stub", "0.1"

    def run(self, in_path, out_path, fidelity):
        content, _ = read(in_path)
        nonce = os.urandom(8)                   # nondeterministic per run
        with open(out_path, "wb") as f: f.write(pack(content, [(0xFE, nonce)]))
        return ScrubResult(ok=True, out_path=out_path, fidelity=fidelity)
