# Test corpus (Phase 0)

Fuel for the differential-testing harness. Per format, files with metadata injected into **every known locus** — so validation proves all duplicate copies are cleared, not just the named tags.

## Build it two ways
- **Synthetic injection** — start from a clean/minimal file and write metadata into each locus programmatically (EXIF, XMP, IPTC, ICC, embedded thumbnail with its own EXIF, ...). Known ground truth, systematic coverage. Track the **generators**, not the outputs.
- **Real-world samples** — actual phone photos, real Word DOCX, etc. Messy redundant metadata that surfaces loci you wouldn't think to synthesize.

## How pairs/sets come out of it
- A **pair** = one content base stamped two different ways → feeds the F1/F2/F3 differential test.
- An **A2 peer set** = one content base stamped N ways from a single "source profile" → feeds the n-way fingerprint test.

## Note
The binaries themselves are gitignored (large, potentially sensitive). Commit generators and manifests so the corpus is reproducible without storing the files.
