# What we can't do yet — the honest limits

This is the single place where every known limit of the tool is written down in plain
words. It is **the source of truth**: the QA report published on every CI run embeds
the table below verbatim, so a limit recorded here reaches every reader automatically
and a limit *not* recorded here is invisible — which is exactly why it belongs here
first.

The rule behind it: **we never claim a file is untraceable unless we have actually
proven it.** Where a limit is a law of physics, we say so instead of pretending it is
solved. Where our own claim turned out to be broader than our evidence, we write that
down too, rather than quietly narrowing it.

Status key: ✅ solved · 🟡 bounded (small, measured, documented) · 🔴 open (known gap,
not yet fixed) · ⚪ out of scope (impossible, or outside the threat model) ·
🔜 planned.

<!-- LIMITS:BEGIN -->
| # | The honest limit (plain words) | What we do about it | Status |
|:-:|---|---|:--:|
| **1** | Even after every tag is deleted, the *way* a file was compressed leaves a hidden signature that can hint which app, phone or program made it. | We re-save through **one standard encoder** so every file we produce shares the same signature. For **PNG** and **FLAC** this costs **nothing at all** — identical pixels, identical audio, right down to the last bit. **JPEG** and **MP3** need a re-recording that costs a little quality, because their signature is baked into the compressed data rather than sitting in a separate layer. For MP3 it is proven against a genuinely *different* encoder, not just a different program using the same one. | ✅ **Solved** |
| **2** | Our "every file comes out looking the same" promise is **not one single group**. Low-quality-rate audio can't legally use our standard setting, so it comes out at a different one — a handful of groups, not one. Someone can see *which* group a file belongs to. | **Deliberate, and measured.** The group is decided by the recording's own sample rate, which we **preserve on purpose** — changing it would alter the audio, and keeping the sound untouched matters more. What must not leak is *who made the file*, and that is tested **inside every group separately**: at both 44.1 kHz and 22.05 kHz, no producer can be told from another after F3, in the file's structure **and** in its sound. | ✅ **Solved per group** |
| **3** | Some unusual audio files we **refuse to process at all**: older Layer II audio wearing an `.mp3` name, and files using a rare "free-format" bitrate. | The tool **fails closed** — it stops rather than write a file it cannot fully vouch for. Nothing leaks, but the user gets no output. Broader format support is planned. | 🟡 **Safe but incomplete** |
| **4** | Making a file untraceable means **re-recording** it, and that always costs something: a high-quality audio file loses some quality, and a very small file can come out several times **larger**. | An unavoidable trade of this approach, stated up front rather than hidden. The alternative — keeping the original audio untouched — is exactly what leaves the traceable signature in place. | 🟡 **Bounded trade-off** |
| **5** | Every camera sensor leaves a faint, unique **noise pattern baked into the pixels** (PRNU). Audio has the same problem: every **microphone and room** leaves a fingerprint, and mains electricity hums at a frequency that can date a recording. | These live **in the picture and in the sound itself**, not in the metadata — removing them would destroy the content. They are a known limit of **every tool that exists** and sit outside our medium-tier threat model. We document them openly. | ⚪ **Out of scope** |
| **6** | A **more sophisticated investigator** than the one we built might still succeed. Our audio test inspects the sound's frequency profile; published research also inspects the encoder's internal number-crunching, which we have **not** tested against. | We report what we measured and what we did not. We do **not** claim to survive attacks we never ran — the untested methods are named so the gap is visible rather than implied away. | 🔴 **Untested** |
| **7** | A second style of audio VBR header used by some encoders is **unhandled and untested**. | On the roadmap; files that use it are refused rather than half-cleaned. | 🔜 **Planned** |
| **8** | We currently handle **JPEG, PNG, MP3, FLAC and M4A**. | Next, on the same tested foundation: 📄 documents (PDF / Word) → 🎬 video (MP4) → 📷 camera RAW. Each ships only when its differential tests pass. | 🔜 **Planned** |
| **9** | A cleaned file is **recognisably cleaned**. Everything we produce comes out in the same standard shape, so someone can tell a scrubber was used — they just cannot tell *which program, phone or camera* made the original, or what was removed. | Unavoidable, and the point: looking identical to every other cleaned file is precisely what makes yours untraceable. Hiding that a file was cleaned is a different problem from hiding where it came from, and only the second one is what this tool promises. | ⚪ **Inherent** |
| **10** | For **M4A** files we clean everything hidden, and we make the *packaging* anonymous — but if the original recording was made at a different quality setting, a faint trace of that earlier setting stays in the sound. | Re-recording cannot undo what the first recording already threw away; this is the same limit as re-saving a photo. Files that started from the same recording come out **byte-for-byte identical**, so they cannot be told apart at all. We have not yet measured whether the remaining trace can actually identify a *producer*, so it is written down as an open question rather than counted as solved. | 🔴 **Open — not yet measured** |
<!-- LIMITS:END -->

---

## Where the evidence lives

<!-- RESIDUALS:BEGIN -->
- **DQT (JPEG quantization tables):** the F1/F2 fingerprint source — neutralised at F3 via a single standard encoder.
- **LAME/Xing header + bitrate contour (MP3):** the audio equivalent — survives F1, normalised at F3.
- **FLAC block size, framing and vendor string:** survive F1, normalised at F2 with **bit-identical audio** (STREAMINFO's MD5 of the unencoded samples proves it). The vendor string ("reference libFLAC…", "Lavf…") names the encoder outright but sits in a metadata block, so unlike MP3's LAME tag it costs nothing to remove. FLAC and PNG are the two formats where A2 is free.
- **Our own canonical form:** a scrubbed FLAC carries exactly one metadata block, so `fLaC` + the STREAMINFO header is constant across outputs; the MP3 canonical LAME header is the same kind of constant. Recorded in each matrix's `excluded` block and as limit #9 — it marks a file as scrubbed, never as scrubbed-from-what. Two earlier candidates were removed rather than excused after the fingerprint guard caught them: a fixed empty Vorbis comment and 8 KiB of fixed zero padding.
- **Cross-engine audio trace (MP3):** measured, not assumed. A classifier that identifies the source encoder from the sound alone scores **0.94 on untouched and F1 files, and chance (0.50) after F3**. Peer set includes `shineenc`, an encoder that is not libmp3lame. Scope: frequency-profile features; MDCT-domain classifiers untested (limit #6).
- **Anonymity groups (MP3 F3):** the standard 192 kbps setting is illegal for MPEG-2 sample rates, so those files emit at 160 kbps instead — separate groups (limit #2). Producer-anonymity is verified **within each group**, in header space (E-LAME, both rates pass) and in audio space (E-ENGINE: 44.1 kHz 0.94 → 0.50, 22.05 kHz 1.00 → 0.61, against chance 0.50). The group boundary follows the source's sample rate, which is content, not a producer trait.
- **PRNU (sensor noise) / microphone + room response / mains hum:** structural impossibilities; documented, out of scope (limit #5).
- **M4A muxer layout vs coded audio:** two producers in one file. F2 normalises the layout (box order, brand, `free` slack) losslessly; F3 additionally collapses same-source producers to byte-identical output. The primary-encoding trace from a differently-encoded original survives both, and is recorded as an open residual (limit #10) because no audio-space classifier has been run for M4A yet — the MP3 E-ENGINE treatment is what would settle it.
- **Primary-quantization estimate:** the bounded "was re-saved" hint after F3 — reveals nothing about original metadata.
- **Scrubber-fingerprint guard:** confirms our *own* tool leaves no producer/creator string, no odd padding, deterministic ordering, and no modification-time stamping.
<!-- RESIDUALS:END -->

Machine-checked detail for each format sits in the Pareto matrices under
`tests/harness/results/*.json`, where every cell carries its verdict *and* the reason
and residuals behind it. The experiments that produced them:

| Experiment | Question it answers | Code |
|---|---|---|
| **E3 (DQT)** | Does the JPEG compression signature identify the producer? | `tests/scrub/e3_dqt.py` |
| **E-LAME** | Do MP3 *headers* still identify the encoder after scrubbing? | `tests/scrub/e_lame.py` |
| **E-ENGINE** | Does the *sound itself* still identify the encoder after scrubbing? | `tests/scrub/e_engine.py` |

E-ENGINE asserts its own controls: if the classifier cannot identify the encoder on
untouched files, the result on scrubbed files is reported as **inconclusive** rather
than as a pass. A test that cannot detect the thing it is looking for proves nothing.

## Adding a limit

Add a row above, and it appears in the next CI report automatically — the report reads
this file. Keep the wording plain: the audience includes people who do not work with
file formats. State the limit first, what we do about it second, and never let the
"what we do" column imply a fix that does not exist.
