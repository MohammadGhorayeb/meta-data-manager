# Phase 1 Plan — Image Leaves: JPEG → PNG

Execution plan for the first real format handlers. Everything here plugs into the
Phase 0 harness: our tool is a `Scrubber` (wrapped by `SubprocessScrubber` via a
`scrub {in} {out} {fidelity}` CLI), format knowledge lands as `FormatPlugin`
implementations, and "done" is a validated Pareto matrix in
`tests/harness/results/`, never inspection.

**Pareto targets (from the implementation plan):**
- JPEG: pass A1 at F1 and F2 across *all* loci; demonstrate (not assume) that A2
  requires F3 via the DQT/encoder fingerprint; ship an F3 path that passes A2
  minus documented residuals (PRNU, recompression trace).
- PNG: pass A1 at F1; pass A2 at F2 if the deflate-normalization experiment (E5)
  supports it — PNG is the one Tier-1 format where A2-without-F3 may be reachable,
  because "re-encode" of IDAT is lossless by construction.

---

## 0. Ground truth: what exists and what's missing

Exists: the differential harness (floor / A1 / A2 / fingerprint-guard oracles),
synthetic + real-seed corpus machinery, matrix writer, stub scrubbers.

Missing (blocks P1, so it's work item W0 — this is the scrubber-side half of the
Phase 0 spine that was deferred): `src/` entirely — dispatch skeleton, shared
standard modules, CLI. The harness can only test a black box that exists.

---

## 1. Work items (dependency order)

### W0 — Scrubber-side spine (`src/`)
- `src/scrub/cli.py` — `scrub <in> <out> --fidelity F1|F2|F3`. Exit nonzero on
  any parse failure or unhandled format: **fail closed, never emit a
  half-scrubbed file.**
- `src/scrub/dispatch.py` — magic-number sniff → handler registry. JPEG
  `FF D8 FF`, PNG `89 50 4E 47 0D 0A 1A 0A`. Sniff on leading bytes, never
  extension.
- `src/scrub/standards/tiff_ifd.py` — EXIF/TIFF-IFD parser. Byte order (II/MM),
  IFD walk (IFD0 → EXIF IFD → GPS IFD → interop IFD → **IFD1 = thumbnail**,
  which has its own tag set including its own GPS). P1 policy is
  drop-the-whole-container, so the parser's Phase-1 job is *verification and
  locus annotation*, not surgical tag editing — surgical mode comes when RAW
  (P4) needs it. Design the API for both from day one:
  `parse(buf) -> IfdTree`, `loci(tree) -> list[(offset, len, name)]`.
- `src/scrub/standards/xmp.py` — XMP packet detection (`http://ns.adobe.com/xap/1.0/`
  header in JPEG APP1; `XML:com.adobe.xmp` iTXt in PNG), plus **ExtendedXMP**
  (`http://ns.adobe.com/xmp/extension/`, GUID + offset per APP1 segment,
  reassembly across segments).
- `src/scrub/standards/icc.py` — ICC profile handling. In JPEG it spans multiple
  APP2 segments (`ICC_PROFILE\0` + seq/total bytes). Policy decision recorded
  here once, used everywhere: **strip the profile, because ICC headers carry
  creator/platform/date fields and the profile byte pattern is itself an A2
  vector.** Content-preservation consequence: an image whose pixels are encoded
  in a wide-gamut space renders differently without its profile → in that case
  F1/F2 must either keep a *sanitized* profile (zeroed header meta, fixed
  profile ID) or refuse; F3 converts to sRGB. Sanitize-vs-strip is experiment E7.
- `src/scrub/standards/iptc_iim.py` — IPTC-IIM inside APP13 Photoshop `8BIM`
  resources (resource 0x0404), plus the rest of the 8BIM resource catalogue
  (0x040C = another embedded thumbnail locus).

Done gate: unit tests parse/round-trip fixture buffers for each standard; the
CLI dispatches a JPEG and a PNG to a stub handler.

### W1 — JPEG segment walker (spec-parse depth)
`src/scrub/formats/jpeg/segments.py`. One honest marker walk used by handler,
plugin, and corpus injector alike:
- Marker stream: SOI, APP0–APP15, COM, DQT, DHT, DRI, SOF0/1/2 (baseline,
  extended, progressive), SOS, entropy-coded data with `FF 00` stuffing and
  RST0–7, EOI. Big-endian 16-bit lengths including themselves.
- Multi-scan progressive files (multiple SOS), multi-frame oddities.
- **Trailer bytes after EOI** — a first-class locus (apps append data there;
  some steganography and WhatsApp variants live there).
- Segment inventory as `(marker, offset, length, sub_id)` where `sub_id`
  disambiguates APP1-EXIF vs APP1-XMP vs APP1-ExtendedXMP, APP2-ICC vs
  APP2-**MPF** (Multi-Picture Format — a *second full JPEG* hidden in the file;
  every modern phone writes one for portrait/motion shots), APP13-8BIM.

### W2 — Harness plugins: `JpegPlugin`, `PngPlugin`
Implement `FormatPlugin` for both, registered in `tests/harness/dispatch.py`:
- `matches()` — magic bytes.
- `annotate(offset)` — map a byte offset to a segment/chunk name via W1's walk
  (this is what turns a raw harness diff into "leak is in APP1/IFD1/GPSLatitude").
- `canonical_content()` — the content-identity definition, the most important
  design decision in P1:
  - JPEG: the decoded pixel buffer via one pinned decoder build (libjpeg-turbo
    version recorded in the matrix). Not the entropy-coded bytes — F2 legally
    rewrites those.
  - PNG: the decoded RGBA buffer. Not IDAT bytes — F2 recompresses them.
- `mandatory_constants()` — magic numbers, IHDR/IEND, SOI/EOI, so the
  fingerprint guard doesn't flag format-required invariants as tool signature.

### W3 — Image corpus builder (`tests/corpus/images/`)
Synthetic-primary, per the harness philosophy: start from pixels we generate
(Pillow gradients + noise so pHash has texture), then inject metadata into
**every locus, one corpus member per locus** so a missed locus is a named test
failure, not a silent gap:

| # | Locus | Injection route |
|---|-------|-----------------|
| L1 | EXIF IFD0/EXIF-IFD tags | piexif / exiftool |
| L2 | EXIF GPS IFD | piexif |
| L3 | **IFD1 thumbnail + its own EXIF/GPS** | piexif `thumbnail` + exiftool `-ifd1:` |
| L4 | XMP packet | exiftool |
| L5 | ExtendedXMP (>64KB payload spanning APP1s) | exiftool with oversized XMP |
| L6 | IPTC-IIM in APP13 | exiftool `-iptc:` |
| L7 | Photoshop 8BIM thumbnail (0x040C) | exiftool / crafted bytes |
| L8 | ICC profile (with distinctive header fields) | Pillow `icc_profile=` |
| L9 | COM segments (multiple) | crafted bytes |
| L10 | JFIF APP0 thumbnail | crafted bytes |
| L11 | Adobe APP14 | save via Photoshop-flavored encoder or craft |
| L12 | **MPF second image in APP2** | crafted from spec / phone sample |
| L13 | Trailer bytes after EOI | append |
| L14 | PNG tEXt / zTXt / iTXt (incl. XMP iTXt) | Pillow `PngInfo` |
| L15 | PNG tIME, eXIf, iCCP | Pillow / exiftool / crafted |
| L16 | PNG private/unknown ancillary chunks + bytes after IEND | crafted |

Every member content-verified by `corpus/content_identity.py` before use.

A2 peer corpus: the same pixel content exported through **n≥6 real producers**
(macOS Preview, Pillow, libjpeg-turbo cjpeg, GIMP, ImageMagick, an iPhone/
Android camera sample re-shot of a test chart; PNG: Pillow, optipng, pngcrush,
ImageMagick, macOS screenshot) → `make_a2`-style source sets. Real-seed
secondary corpus: a handful of genuine phone photos in
`~/metadata-research/step2/originals/`, `pytest.skip`-guarded as in Phase 0.

### W4 — JPEG F1 handler (raw byte surgery)
`src/scrub/formats/jpeg/f1.py`. **Keep-list, not drop-list** (the benchmark
whitelist approach, done right): copy only SOI, DQT, DHT, DRI, SOF*, SOS +
entropy data, EOI. Everything else — all APPn, COM, trailer — is dropped by
default. Conditional keeps, each a recorded policy:
- APP0/JFIF: emit a minimal canonical JFIF v1.01 unit-density header (some
  decoders want one) with no thumbnail — *identical bytes for every input*
  (mandatory-constant, declared to the guard).
- APP14/Adobe: **transform-critical.** For CMYK/YCCK JPEGs, dropping APP14
  changes color interpretation → content violation. Keep a canonicalized APP14
  (transform flag only, version/flags zeroed) when SOF component count/IDs
  require it; declare it mandatory-constant.
- ICC: per W0 policy — strip for sRGB-safe images, sanitized profile or refusal
  otherwise (E7 decides the default).
No length or checksum recomputation needed (JPEG has none) — that binary work
arrives with PNG CRCs. Entropy-coded bytes are copied verbatim: F1 = pixel
bitstream untouched.

### W5 — JPEG F2 handler (lossless re-encode)
Rewrite the entropy coding without touching quantized coefficients (what
`jpegtran -optimize` does). Decision (E3 informs it): shell out to a pinned
`jpegtran` build vs. decode-coefficients-and-re-Huffman in Python. Either way:
- DHT tables regenerated canonically → kills the Huffman-table fingerprint.
- Scan script normalized (baseline, fixed component order) → kills the
  progressive-scan-script fingerprint.
- **DQT survives by definition** — you cannot change quantization losslessly.
  This is the documented F2 residual that keeps A2 red (Kornblum).
- Restart markers stripped/normalized (DRI presence is an encoder tell).

### W6 — JPEG F3 handler (lossy re-encode)
Decode with the pinned decoder → re-encode with one canonical encoder +
settings (libjpeg-turbo, fixed quality, fixed standard-annex quant tables,
4:2:0, optimized baseline). Every output claims the same encoder — that is the
point (the A2 defense is *anonymity within the scrubbed class*, which the guard
verifies is not itself a unique tool signature… it will be, and we document
that trade-off explicitly: A2 hides the *original* producer; the scrubber class
is knowable. That is true of every F3 tool and goes in the matrix notes).
Content preservation via the harness pHash oracle with a threshold calibrated
per §4 of the harness README (re-encode same content twice → floor → threshold
just above). Residuals documented: PRNU (structural impossibility, Lukáš),
primary-quantization survival (Sorell, bounded by E4).

### W7 — PNG handler
`src/scrub/formats/png/`. Chunk walk with per-chunk CRC32 verify.
- **F1:** keep-list of chunks. Critical: IHDR, PLTE, IDAT, IEND. Ancillary but
  **render-affecting — keep**: tRNS (transparency!), gAMA, cHRM, sRGB, sBIT,
  PLTE-related hIST? (no — hIST drop is safe), bKGD (viewer-dependent; decide in
  E6b), pHYs (density; drop, it's metadata), acTL/fcTL/fdAT (APNG animation =
  content). Drop: tEXt, zTXt, iTXt, tIME, eXIf, iCCP (same ICC policy as W0),
  sPLT, hIST, oFFs, sCAL, sTER, private chunks, anything after IEND.
  **Recompute nothing except positions** — chunk CRCs are per-chunk, dropping a
  chunk doesn't invalidate others. First real integrity-field work is verifying
  CRCs on input and recomputing when we *modify* a kept chunk (sanitized iCCP).
- **F2:** decode → re-encode pixels losslessly with one canonical pipeline:
  fixed zlib level/strategy/window, fixed filter heuristic (or filter=0),
  single IDAT chunk, canonical chunk order, canonical bit depth/color type
  mapping (e.g. strip redundant alpha? no — that can change content; keep exact
  pixel semantics). This normalizes the deflate/filter fingerprint — the PNG
  analogue of DQT, except here it's *removable losslessly*. If E5 confirms
  peer-set indistinguishability, PNG A2@F2 = pass: the headline result of P1.
- PNG F3 = not_applicable (lossless format; F3 adds nothing over F2).

### W8 — Fingerprint-guard hardening of our own output
Run `fingerprint_guard.evaluate` over diverse inputs for every handler×fidelity.
Predicted flags to engineer out: canonical JFIF/APP14 units (declare as
mandatory constants only if truly format-required — the JFIF unit is our
choice, so it *is* a tool signature; mitigation: byte-match what the majority
producer class emits), zlib header bytes in PNG (78 9C etc. — match the most
common level), chunk order (use the dominant real-world order, not an unusual
"sorted" order). **Rule: normalize toward the crowd, not toward elegance.**

### W9 — Benchmarks under identical harness runs
`SubprocessScrubber` wrappers: `exiftool -all= -o {out} {in}`, `mat2` (F1-ish),
plus `jpegtran -copy none` as a semi-scrubber. Produce their matrices over the
same corpus. Expected findings to verify, not assume: exiftool leaves trailer
bytes and some 8BIM resources in edge cases; MAT2's PNG path re-encodes (check
what its Cairo pipeline does to content identity); neither clears MPF. Their
red cells are the funding-proposal evidence; ours must be green at the same
(A,F) points.

---

## 2. Experiments (each = hypothesis → method → matrix/doc consequence)

- **E1 — Determinism floor.** Our handlers, N=5 repeats per input. Hypothesis:
  fully deterministic (empty floor) at F1/F2/F3. Any variable locus is a bug
  (unseeded zlib? dict ordering? mtime in output?). Gate for everything else.
- **E2 — Thumbnail/GPS survival in benchmarks.** Inject GPS into IFD1 (L3) and
  8BIM 0x040C (L7); run exiftool/MAT2. Hypothesis: at least one benchmark path
  leaves a thumbnail locus in some mode. Evidence for proposal; regression test
  for us.
- **E3 — DQT fingerprint (reproduce Kornblum).** A2 peer corpus (W3, n≥6
  producers), F1/F2 scrub, harness peerset oracle. Hypothesis: DQT bytes alone
  classify the producer with high accuracy → A2@F1/F2 = fail, *empirically*,
  which is what lets the matrix say "A2 requires F3" as fact not citation.
- **E4 — Recompression trace (Sorell bound).** After F3, can first-generation
  quantization be estimated from coefficient histograms? Method: existing
  literature technique on our F3 outputs across producers. Outcome: a bounded
  residual note on the F3 A2 cell (pass-with-residual), not a silent pass.
- **E5 — PNG deflate normalization kills A2 (the headline experiment).** Same
  pixels through 5+ PNG producers → our F2 → peerset oracle. Hypothesis:
  post-normalization members are byte-identical (strongest form) or at least
  indistinguishable beyond floor. If byte-identical: PNG A2@F2 = pass, first
  green A2 cell in the project. If not: enumerate surviving separators
  (interlace flag? color-type choices?) and iterate.
- **E6 — Render-identity of the PNG keep-list.** Decode L14–L16 members before/
  after scrub (including tRNS/gAMA/bKGD edge cases) with 2 decoders. Any pixel
  diff → keep-list amended. (E6b: decide bKGD by whether reference viewers
  composite it.)
- **E7 — ICC sanitize vs strip.** Wide-gamut (Display-P3) corpus member.
  Compare: strip / sanitize-in-place (zero dates, creator, platform, profile ID
  recomputed per spec §… MD5) / convert-to-sRGB (F3 only). Measure render diff
  and peerset distinguishability of sanitized profiles. Outcome: locked policy
  in W0 module, same policy inherited later by PDF/TIFF/HEIC.
- **E8 — MPF + trailer + ExtendedXMP adversarial members.** The three loci most
  likely to expose "named-tag" scrubbers (L5, L12, L13). Verify our walker
  finds them, our F1 drops them, benchmarks' results recorded.
- **E9 — Content identity at each fidelity.** F1: output entropy bytes ==
  input entropy bytes (byte compare of ECS spans). F2: decoded pixels
  bit-identical pre/post (pinned decoder). F3: pHash within calibrated
  threshold + visual spot-check sheet. Wired as pytest, not one-off.
- **E10 — Our guard run** (W8 formalized): diverse-input invariants of our own
  outputs, minus declared mandatory constants, must be empty.

---

## 3. Execution order & gates

1. **M0 (W0):** spine modules + CLI + dispatch. Gate: unit tests green.
2. **M1 (W1–W3):** walker, plugins, corpus. Gate: every L1–L16 member
   content-verified; `annotate()` names every injected locus; E1 floor run on
   `exiftool -all=` (borrowed scrubber) proves the image plugins work end-to-end
   before our handler exists.
3. **M2 (W4):** JPEG F1. Gate: A1@F1 pass across L1–L13, E9-F1, E10.
4. **M3 (W5 + E3):** JPEG F2 + DQT experiment. Gate: A1@F2 pass; A2@F1/F2
   recorded fail with DQT evidence.
5. **M4 (W6 + E4):** JPEG F3. Gate: A2@F3 pass-with-residuals; pHash guard.
6. **M5 (W7 + E5/E6):** PNG F1+F2. Gate: A1 pass; E5 verdict recorded either way.
7. **M6 (W9):** benchmark matrices. Gate: side-by-side matrix table for docs/
   proposal.
8. **Exit:** `results/jpeg_ours.json`, `results/png_ours.json` + benchmark
   matrices validate against the schema; residuals (DQT, PRNU, recompression
   trace, scrubbed-class signature) documented in cells; framework.md
   conclusions updated only where empirically confirmed.

## 4. Decisions to confirm before the relevant milestone (not now)
- F2 mechanism: pinned `jpegtran` subprocess (fast, battle-tested, but its own
  fingerprint + shipping dependency) vs. native coefficient re-Huffman (slower
  to build, fully controlled). Decide at M3 with E3 data.
- ICC default (E7). — PNG bKGD/pHYs keep-list edges (E6). — Progressive input
  handling at F1: keep scan structure verbatim (it's content-adjacent) vs.
  refuse; F2 normalizes it regardless.

## 5. Failure modes to watch (from framework.md, made local)
- A keep-list that silently keeps an unknown APPn on parse confusion → fail
  closed instead.
- Plugin and handler sharing one walker means a walker bug hides from the
  differential test (same blind spot both sides). Mitigation: harness-side
  `annotate` cross-checked against `exiftool -htmlDump`-style independent
  parse for corpus members (one-time verification script, M1).
- Canonical-unit choices (JFIF header, zlib level) becoming *our* fingerprint —
  W8's "normalize toward the crowd" rule; guard is the enforcement.
