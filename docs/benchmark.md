# Benchmark — Irreversible Metadata Scrubber vs standard tools

_Comparison against the tools the field already uses (W9). Findings were reproduced and adversarially verified. Not about removing *more* metadata — mature tools do that well — but about **what it costs you** and **what it proves.**_

## Capability matrix

| Capability | Ours | ExifTool | MAT2 | jpegtran |
|---|:--:|:--:|:--:|:--:|
| Removes all named metadata (EXIF/GPS/XMP/IPTC/thumbnail/trailer/MPF) | ✅ | ✅ | ✅ | ✅ |
| Lossless option — JPEG pixels byte-identical | ✅ F1/F2 | ✅ | ❌ always lossy | ✅ |
| No cumulative quality loss when re-run | ✅ | ✅ | ❌ degrades each pass | ✅ |
| Preserves CMYK colour | ✅ F1/F2 | ✅ | ❌ converts to RGB | ✅ |
| Preserves progressive JPEG intact | ✅ F1/F2 | ✅ | ❌ lossy re-encode | ✅ |
| Erases the encoder fingerprint (untraceable, A2) | ✅ F3 / PNG-F2 | ❌ | ⚠️ only via forced lossy | ❌ |
| Lossless **and** untraceable (PNG) | ✅ | ❌ | ⚠️ re-encodes | n/a |
| You choose the fidelity for the threat you face | ✅ F1/F2/F3 | ❌ | ❌ | ❌ |
| Measured (adversary × fidelity) guarantee matrix | ✅ | ❌ | ❌ | ❌ |
| Differential-test verified · fails closed | ✅ | ❌ | ❌ | ❌ |
| Documents impossible residuals honestly (PRNU) | ✅ | ❌ | ❌ | ❌ |
| **Handles M4A audio at all** | ✅ F1/F2/F3 | ⚠️ tags only | ❌ **refuses the format** | n/a |
| **Clears a FLAC APPLICATION block** (arbitrary third-party payload) | ✅ | ⚠️ not by default | ❌ **leaves it intact** | n/a |
| Removes the FLAC vendor string (names the encoder) | ✅ | ⚠️ manual | ❌ leaves it | n/a |
| **Lossless *and* untraceable (FLAC)** | ✅ F2 | ❌ | ❌ | n/a |
| Erases the audio encoder fingerprint, measured cross-engine | ✅ MP3 F3 | ❌ | ❌ | n/a |

**The one-line takeaway:** every tool deletes tags. Only ours lets you keep the picture *pixel-perfect* when you want fidelity, become *untraceable* when you want anonymity, and backs both with a measured, verified matrix.


### Audio: where the other tools stop

Measured with **MAT2 0.14.0** on this project's own torture corpus
(`tests/scrub/m4a_corpus.py`, `tests/scrub/flac_corpus.py`), reproduced with
`mat2 --inplace <file>`.

**M4A — MAT2 refuses it outright**: `bm.m4a's format (audio/mp4a-latm) is not
supported`. Not a weaker clean — no clean at all, so a user handed an `.m4a` gets
nothing back.

ExifTool does accept it and removes every planted tag — but running
`exiftool -all= ` on the same file leaves, measured with our own residual check:

| Survivor | What it is |
|---|---|
| `mvhd` / `tkhd` / `mdhd` creation + modification times | **when the file was made** — structural fields, not tags, so a tag-oriented pass does not touch them |
| a `free` box | dead space that can hold arbitrary bytes, and whose size is a muxer tell |

Ours removes both, patches the sample tables so the audio still decodes once the
metadata is cut out, and normalises the muxer layout losslessly at F2.

**FLAC — MAT2 accepts it but leaves two things behind.** On a file carrying an
`APPLICATION` block, cover art, Vorbis tags and an ID3v2 prefix, MAT2's output still
contained:

| Survivor | What it is |
|---|---|
| `MOEX` + `app-hidden-SECRET` | the **APPLICATION block payload** — arbitrary third-party data, passed straight through |
| `Lavf62.12.101` | the **vendor string**, which names the encoder that made the file |

Our F1 and F2 outputs contained none of the planted secrets. The APPLICATION block is
the more serious of the two: it is a general-purpose container that any application
may write anything into, so leaving it intact means a privacy tool has forwarded
data it never inspected.

FLAC is also the audio headline for fidelity: **A2 with bit-identical audio**, the
only lossless route to untraceability in the phase, matching what PNG does for
images.

## Evidence 1 — content preservation (a normal JPEG)

| Tool | Pixels byte-identical? | Quality |
|---|:--:|---|
| **Ours** (F1) | ✅ yes | lossless |
| **Ours** (F2) | ✅ yes | lossless |
| **Ours** (F3) | ❌ no | lossy re-encode |
| ExifTool | ✅ yes | lossless |
| MAT2 | ❌ no | lossy re-encode |
| jpegtran | ✅ yes | lossless |

## Evidence 2 — repeated cleaning (5 passes, chained)

_Real workflows scrub a file more than once. Lossless tools are stable; MAT2 degrades a little more every pass (generational loss)._

| Tool | RMSE from original after 1 / 3 / 5 passes |
|---|---|
| **Ours** (F1) | 0.0 / 0.0 / 0.0 |
| ExifTool | 0.0 / 0.0 / 0.0 |
| MAT2 | 6.98 / 9.65 / 11.45 |
| jpegtran | 0.0 / 0.0 / 0.0 |

## Evidence 3 — awkward-but-valid JPEGs

| Tool | CMYK colour preserved? | Progressive JPEG survives intact? |
|---|:--:|:--:|
| **Ours** (F1) | ✅ CMYK | ✅ lossless |
| **Ours** (F2) | ✅ CMYK | ✅ lossless |
| **Ours** (F3) | ❌ → RGB (colour changed) | ⚠️ lossy (by design) |
| ExifTool | ✅ CMYK | ✅ lossless |
| MAT2 | ❌ → RGB (colour changed) | ❌ lossy re-encode |
| jpegtran | ✅ CMYK | ✅ lossless |

## Evidence 4 — the fingerprint snoop (A2)

_Same image from 3 different encoders. "Untraceable" = all outputs share one compressor fingerprint (DQT)._

| Tool | Distinct fingerprints across 3 producers | Verdict |
|---|:--:|---|
| **Ours** (F3) | 1 | ✅ untraceable |
| ExifTool | 3 | ❌ traceable (3 fingerprints) |
| MAT2 | 1 | ✅ untraceable |
| jpegtran | 3 | ❌ traceable (3 fingerprints) |

## Evidence 5 — metadata removal parity (the torture file)

_All loci at once: EXIF/GPS + embedded thumbnail, XMP, ExtendedXMP, ICC, IPTC/8BIM, MPF, COM, and a post-EOI trailer. Mature tools are strong here — we are at parity, which is the honest finding._

| Tool | Metadata tags left | Trailer bytes |
|---|:--:|:--:|
| **Ours** (F1) | 0 | 0 |
| **Ours** (F2) | 0 | 0 |
| **Ours** (F3) | 0 | 0 |
| ExifTool | 0 | 0 |
| MAT2 | 0 | 0 |
| jpegtran | 0 | 0 |

## Evidence 6 — PDF revision history (Phase 3)

_A PDF is edited by **appending**: new objects, new cross-reference table, a trailer pointing back at the old one. Nothing is deleted. Truncate the file after any earlier `%%EOF` and the earlier draft opens as a document. This is the disclosure mode behind every 'redacted report published with the original text underneath'._

_Corpus: a 3-revision document. Revision 1 is confidential, revision 3 is the public text. Attacks: revision rollback, raw carving, and an object ledger._

| Tool | Revisions left | Stale objects | Text kept | Recoverable by rollback |
|---|:--:|:--:|:--:|---|
| _(untouched — the control)_ | 3 | 4 | ✅ | `CONFIDENTIAL-REV1-SECRET`; `CONFIDENTIAL-REV2-SECRET`; `Author-REV1-SECRET`; `Draft-REV1-SECRET`; `/Author=Author-REV2-SECRET`; `/Title=Draft-REV2-SECRET` |
| qpdf/pikepdf rewrite | 1 | 0 | ✅ | ✅ nothing |
| MAT2 (default) | 2 | 9 | ❌ destroyed | `/CreationDate=D:2026…+03'00`; `/Producer=cairo 1.18.4 (https://cairographics.org)` |
| MAT2 `--lightweight` | 2 | 9 | ✅ | `/CreationDate=D:2026…+03'00`; `/Producer=cairo 1.18.4 (https://cairographics.org)` |
| ExifTool `-all=` | 4 | 5 | ✅ | every planted secret, plus the whole `/Info` it "cleared" |

**ExifTool edits a PDF by appending an incremental update**, so `-all=` *adds* a revision and removes nothing — it says so itself: "PDF edits are reversible. Deleted tags may be recovered!" Every original value is still there.

**MAT2 re-renders**, so the document's own history genuinely goes — a real result, and the reason it beats ExifTool on this axis. But it then clears `/Info` by appending an incremental update of its own, leaving its producer string (`cairo …`) and a **wall-clock creation date with the operator's UTC offset** one revision down. Rolling back names the tool that made the file and the second it was made. Reproduced on a real 295 KB report as well as on the synthetic corpus, on **both** MAT2 paths.

_Our own PDF tier is under construction (Phase 3 M2). The row it has to beat is therefore: collapse to a single revision, keep the text, and leave no producer string or timestamp behind — which no tool measured here does._

_Regenerate this section with `./.venv/bin/python -m tests.scrub.e_pdf_history`; the assertions behind it are in `tests/scrub/test_e_pdf_history.py`._

---
_Generated by `scripts/benchmark.py`. Findings adversarially verified. MAT2's fingerprint normalization is a *side effect* of its forced lossy re-encode, not a tunable guarantee; ExifTool and jpegtran are lossless but leave the fingerprint intact._
