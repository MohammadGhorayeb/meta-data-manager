# Differential-testing harness (Phase 0)

The verification primitive and the definition of "done" for every format. Build before any single format handler.

## What it does
Takes **content-identical, metadata-different** inputs, scrubs each, diffs the outputs, and classifies each diff as **noise** or **leak**. Anything that doesn't converge is content-independent — i.e. surviving metadata or a fingerprint.

## Oracles by fidelity tier
- **F1 / F2** — outputs should be byte-identical. Byte-compare, then localize any diff to a structure (which chunk/atom/segment leaked).
- **F3** — outputs won't match byte-for-byte. Confirm perceptual identity, then check that no extracted field or structural fingerprint *correlates with* the input variant (ExifTool as ground-truth measuring stick).
- **A2** — n-way: a set of same-source files checked for a shared surviving fingerprint, not just a pair.

## Also wired in here
The **scrubber-fingerprint guard**: run the same differential/peer check on the tool's *own* outputs and require no tool-specific signature (producer string, padding habit, mtime, non-standard ordering).

## Exit gate for Phase 0
Harness flags a leak from a deliberately-broken stub scrubber and passes a clean one; the fingerprint guard catches a stub that stamps a version string. Once both hold, "differential test passes" is a signal later phases can trust.
