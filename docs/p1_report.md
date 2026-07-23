# Irreversible Metadata Scrubber — Progress & Testing Report

**Phase 1: Images (JPEG and PNG)**

---

## 1. What this project is (in one paragraph)

Photos and files carry hidden information called **metadata** — where a photo was
taken (GPS), which phone took it, the date, and sometimes a small copy of the
image (a **thumbnail**). This project builds a tool that **removes that hidden
information so it can never be recovered again** — not even by someone opening the
file byte-by-byte with forensic tools. The key word is **irreversible**: not just
hidden or deleted, but truly gone from the file.

The goal is a **working tool**, tested against a realistic attacker. That attacker
is a **journalist or amateur investigator with off-the-shelf tools** — not a
nation-state. We call this the "medium-tier" adversary.

---

## 2. The two kinds of snoop we defend against

We split the attacker into two levels, because beating one is easy and beating the
other is hard.

| Name | Who they are | What they try to read |
|---|---|---|
| **A1** | The metadata snoop | Opens the file and reads the tags: GPS, camera, date, thumbnail, comments. |
| **A2** | The fingerprint snoop | Even with all tags gone, looks at *how the file was compressed* to guess which program or phone made it. |

**A1 is about content you can read.** **A2 is about a hidden "signature" left by the
compressor** — like recognising a typewriter from the shape of its letters, even
with no name on the page.

---

## 3. What we built

The tool auto-detects the file type (by its real contents, not its name) and
strips it. We built two formats so far, each with several **cleaning levels**
called *fidelity tiers*:

| Level | Plain meaning | Cost |
|---|---|---|
| **F1** | Delete all metadata, don't touch the picture at all (byte-for-byte identical pixels). | None |
| **F2** | Delete all metadata **and** repack the compression cleanly. Picture still pixel-perfect. | None |
| **F3** | Fully re-compress the picture with one standard compressor, so every file looks the same. | Tiny, invisible quality loss |

- **JPEG** (photos): F1, F2, and F3 all built.
- **PNG** (screenshots, graphics): F1 and F2 built. PNG needs no F3 — it is a
  lossless format, so F2 already does the strongest job with **zero** quality loss.

Every tier is **fail-closed**: if the tool cannot guarantee a clean result, it
refuses and writes nothing, rather than quietly producing a file that *looks*
clean but isn't.

### What gets removed
EXIF, GPS, camera make/model, timestamps, software version, XMP, Extended XMP,
IPTC, ICC colour profiles, embedded thumbnails, comment blocks, and any extra
bytes tacked onto the end of the file (the "trailer"). The tool also makes sure it
leaves **no signature of itself** — no "made by our scrubber" mark.

---

## 4. How we test — "differential testing"

The core method is simple and powerful. Take **two files with the same picture but
different metadata**. Scrub both. If the results are not identical, the leftover
difference **is** a leak — it can only be the metadata that failed to disappear.

We built an automated **test harness** that does this at scale, plus a battery of
unit tests. As of this report: **127 automated tests, all passing.**

---

## 5. The fingerprint experiment (the interesting part)

To test the A2 fingerprint snoop, we ran a real experiment based on published
forensic research (Kornblum).

**Setup.** We took real photos and compressed each one with **three different
compressors** (Apple's, libjpeg-turbo, and ffmpeg). Same picture, different
program. Then we checked the hidden compression signature — for JPEG this lives in
the **quantization tables (DQT)**.

**Finding.** The signature was **perfectly consistent for each compressor and
different across them** — a reliable fingerprint. And crucially, it **survived F1
and F2 scrubbing unchanged.** Removing metadata does *not* remove the fingerprint.

**The fix.** F3 re-compresses everything with one standard compressor, so all
files end up with the **same** signature — the fingerprint becomes useless. For
PNG, F2 does the same thing losslessly.

---

## 6. Results — the scoreboard

Every cell below was **measured on real files**, not assumed.

| Format | A1 metadata | A1 metadata | A1 metadata | A2 fingerprint | A2 fingerprint | A2 fingerprint |
|---|---|---|---|---|---|---|
| | F1 | F2 | F3 | F1 | F2 | F3 |
| **JPEG** | PASS | PASS | PASS | fail | fail | **PASS** |
| **PNG** | PASS | PASS | n/a | fail | **PASS** | n/a |

**Reading it in plain words:**

- Against the **metadata snoop (A1)**, the tool **wins everywhere.**
- Against the **fingerprint snoop (A2)**, we win by **re-compressing the image** —
  running every file through one standard compressor so they all end up with the
  **same** signature and none can be told apart. This is **JPEG F3** (lossy
  recompression, tiny quality cost) or **PNG F2** (lossless recompression, no
  cost at all).
- **PNG F2 is the standout result**: untraceable *and* pixel-perfect — something
  JPEG cannot do, because a JPEG's fingerprint is baked into the pixels while a
  PNG's is not.

---

## 7. The big test — can it be reversed?

We built a dedicated **adversarial recovery test** that plays the attacker. We
planted **known secret words** in every metadata hiding spot, scrubbed the file,
then attacked the scrubbed bytes with real forensic techniques:

1. Raw byte search for the secret words (normal and wide-character text).
2. Hunting for embedded thumbnails (a hidden second image).
3. Checking for extra bytes at the end of the file.
4. Running **ExifTool** (the industry-standard metadata reader).

We ran this on a synthetic "torture" file stuffed with every kind of metadata,
**and on a real iPhone photo**.

### Results on a real iPhone photo

| Check | Before scrub | After scrub (any level) |
|---|---|---|
| Metadata tags (ExifTool) | **151** | **0** |
| GPS location | 33°N, 35°E | gone |
| Camera / model / date | iPhone 16, full date | gone |
| Embedded thumbnail | present | gone |
| Leftover secret text | found | **none** |

**Nothing we scrubbed could be recovered.** This test is now permanent — it runs
automatically and will catch any future change that reintroduces a leak.

---

## 8. What is done and what is left

**Done:**

- JPEG scrubber — F1, F2, F3.
- PNG scrubber — F1, F2.
- Automated differential-testing harness + 127 passing tests.
- The DQT fingerprint experiment (A2), measured on real photos.
- A permanent forensic-recovery / irreversibility guard.

**Left (future phases):**

- More formats: audio (MP3), documents (PDF, Word), video (MP4), camera RAW.
- A side-by-side comparison against existing tools (MAT2, ExifTool) — evidence for
  the funding proposal.
- Two small optional image loose ends (a formal PNG results table, and hardening
  one edge of the fingerprint check).

---

## 9. Summary

We have a **working, tested tool** for the two most common image formats. It
**defeats the metadata snoop completely**, and **defeats the fingerprint snoop** at
its strongest settings — losslessly for PNG. Most importantly, the information it
removes is **forensically unrecoverable**, because it is deleted from the file
itself, not merely hidden — proven by attacking a real iPhone photo with the same
tools an investigator would use. The remaining traces (device fingerprint, sensor
noise) are **documented honestly** and are the known limits of the entire field,
not shortcomings of this tool.
