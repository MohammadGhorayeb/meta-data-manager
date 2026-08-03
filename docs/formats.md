# How each file type is handled — in plain words

The single source of truth for the **per-format story** in the CI report: for each
file type, what works, what does not, and what we did about it.

Kept here rather than inside `scripts/qa_report.py` for the same reason
`limits.md` is — prose that lives in a script goes stale the moment a phase
ships, and a stale explanation is worse than none because readers take it as
current. One file, read by both humans and the report, cannot drift from itself.

**Names.** These blocks use the reader-facing names for the three cleaning
modes — 🟢 **light clean**, 🔵 **deep clean**, 🟠 **full rebuild** — not the
internal `F1`/`F2`/`F3` codes. Nobody should have to learn the shorthand to read
the report. The mapping between the two is printed once in the report itself.

**What this file does NOT contain:** the pass/fail verdicts. Those are read
directly from the measured Pareto matrices in `tests/harness/results/` every
time the report is built, so the table beside each story is always the real
measurement. If a story here ever contradicts the table next to it, the table is
right and the story needs fixing.

Each block is delimited so the report can pull it out on its own. Adding a new
format means adding a `FORMAT:<id>` block here; if a format publishes results
with no block, the report says so instead of quietly omitting it.

---

<!-- FORMAT:jpeg:BEGIN -->
Every hidden tag comes off in all three modes, and the photo stays
pixel-for-pixel identical in the two gentler ones.

What survives those two is the **compression table** — the set of numbers a
camera or app uses when squeezing a photo. Different makers pick different
numbers, so the table works like a signature. No amount of tag-deleting touches
it, because it is not a tag: it lives inside the compressed picture itself.

**How we solved it:** a **full rebuild** re-saves the photo through one standard
encoder, so every photo the tool produces carries the same table and none of
them stands out. The cost is one round of re-compression — invisible in normal
viewing, but real. That is exactly why the gentler modes still exist, for when
you only need the tags gone.
<!-- FORMAT:jpeg:END -->

<!-- FORMAT:png:BEGIN -->
The same problem as JPEG with a far better ending, and one of the two standout
results in the project.

A PNG's giveaway is *how the file was packed*, not the picture itself — and
packing can be redone from scratch without losing anything at all.

**How we solved it:** a **deep clean** repacks the file one standard way. That
erases the signature completely and hands back **pixel-for-pixel identical**
image data. Untraceable at zero cost — no trade-off to explain, nothing to
weigh up.
<!-- FORMAT:png:END -->

<!-- FORMAT:mp3:BEGIN -->
A **light clean** already removes the tags, including the awkward ones other
tools miss: ID3v1 and v2, APEv2, Lyrics3, GPS-tagged album art, and junk
appended past the end of the audio.

The giveaway is the encoder's own header plus the pattern of bitrates through
the file, which together name the program that produced it.

**How we solved it:** a **full rebuild** re-encodes through one standard
setting — and we check the result two different ways rather than one. In the
file's **structure**, every producer collapses to a single signature. In the
**sound itself**, a classifier that tries to guess the source encoder from audio
alone falls from 0.94 to chance (0.50). The comparison set includes `shine`, a
genuinely different encoder, not just a different program driving the same one.

**Stated honestly:** the promise is *anonymity within a sample-rate group*, not
one universal signature. Low-sample-rate audio cannot legally use the standard
setting, so it lands in its own group. The group follows the recording's own
sample rate, which we preserve on purpose — resampling would change the audio.
Anonymity is verified inside each group separately, never averaged across them.
<!-- FORMAT:mp3:END -->

<!-- FORMAT:flac:BEGIN -->
The second result that costs nothing at all, alongside PNG.

A **light clean** removes the tags and the encoder's vendor string. What is left
is the encoder's choice of block sizes and how it lays out frames — structure
rather than sound, but still a signature.

**How we solved it:** a **deep clean** normalises all of that while the audio
stays **bit-identical**. We do not merely assert that: FLAC stores a checksum of
the original audio inside the file, and we verify the scrubbed file still
matches it. Untraceable, at zero cost.
<!-- FORMAT:flac:END -->

<!-- FORMAT:m4a:BEGIN -->
Tags come off in all three modes. **Untraceable is not reached yet, and we say
so rather than rounding up.**

M4A is unusual: it carries **two makers in one file**. The *muxer* arranged the
container, and the *AAC encoder* produced the sound — so the results above name
which of the two leaked, instead of averaging them into one misleading verdict.

**What we solved:** the container. A **deep clean** normalises box order, brand
and padding while copying the audio across untouched. A **full rebuild** goes
further and produces byte-identical files for sources that were originally
encoded the same way.

**What is still open:** where the original was itself encoded at a different
quality — a 192 kbps source against a 128 kbps one — a trace of that *first*
encoding survives our rebuild. Whether that trace can actually identify who made
the file needs a sound-based test of the kind MP3 has, and we have not built one
for M4A yet. Until we do, the result stays a **fail**. Worth noting: the standard
alternative tool refuses M4A files outright, so everything above is capability it
does not have at all.
<!-- FORMAT:m4a:END -->
