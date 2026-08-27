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
alone falls from **0.89 to 0.53** — indistinguishable from guessing. The
comparison set includes `shine`, a genuinely different encoder, not just a
different program driving the same one.

**Stated honestly:** the promise is *anonymity within a sample-rate group*, not
one universal signature. Low-sample-rate audio cannot legally use the standard
setting, so it lands in its own group. The group follows the recording's own
sample rate, which we preserve on purpose — resampling would change the audio.
Anonymity is verified inside each group separately, never averaged across them.

One more piece of honesty about *how sure* we are: the low-sample-rate group's
evidence is the weaker of the two. Both groups come out indistinguishable from
guessing, but that group sits closer to the line, and an earlier, smaller version
of this test disagreed with itself between two machines. We enlarged the test
until the answer stopped moving, and we report the weaker case as weaker rather
than letting the stronger one speak for both.
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
encoding survives our rebuild.

We have since built the sound-based test for M4A, and it is reassuring: a test
that tries to read the original's quality setting from the **sound alone** gets
it right **88% of the time** on an untouched file, and drops to a **coin flip**
after our full rebuild. What remains is narrower than that, and it is why this
row still reads as a **fail**: the rebuilt file's **size** is still very slightly
different (about 1.4%), because audio that started life at a higher quality
re-compresses to a slightly different size. Everything else about the file is
identical.

That is a genuine difference and we count it rather than explain it away — but
two things bound it. Our comparison holds the *audio itself* constant, which
makes size an unusually clean signal here; among real files of different lengths
and material it is far weaker. And we could close it entirely by padding every
file out to a fixed size, at the cost of making files bigger — a trade we have
not taken, and would rather state than make quietly.

Worth noting: the standard alternative tool refuses M4A files outright, so
everything above is capability it does not have at all.
<!-- FORMAT:m4a:END -->

<!-- FORMAT:pdf:BEGIN -->
**All three modes now exist**, and every tick and cross beside them was measured
on five real programs printing the same document — Chrome, LibreOffice, macOS
Preview and two synthetic producers built to differ on purpose.

PDFs have a problem the other formats don't. **A PDF is edited by adding to the
end of it, not by rewriting it** — so every earlier draft of a document is still
inside the file. Open it and you see the latest version; cut the file at an
earlier stopping point and the old draft opens like a normal document. This is
the leak behind most published "redaction" disasters.

**What we solved.** We don't edit the file, we **rebuild it from scratch**, so
old drafts are never copied across — there is no deletion step that could miss
one. We write every byte of the new file ourselves rather than letting a library
save it, because every PDF library stamps its own signature into the file's
opening line — we measured five of them. The clean reaches inside embedded photos
too, removing their GPS and camera data while leaving the actual image untouched,
bit for bit, and it reaches images that are painted directly into the page and
that no ordinary scan of the file can even see.

Measured against the standard tools on a document with three hidden drafts: the
most widely used one **removes nothing at all** — it also edits by adding to the
end, so it leaves a *fourth* version and every earlier draft intact. The other
one does destroy the history, but leaves its own name and a **clock timestamp
with the operator's timezone** one layer down.

**What the deep clean added.** With the tags gone, what still gives a document
away is the *way the page was typeset*. That splits in two, and we normalised the
half that can be normalised: every instruction that paints the page is now
rewritten through one single writer, so **four of the five programs come out
speaking an identical language**, and two that differed only in *how* they wrote
the same page become indistinguishable. The pages still render **byte-identical**
to the originals — we checked by rendering both to pictures, not just by trusting
our own rule.

**What is still open, and why it is not a bug.** The other half is **where each
letter physically sits on the page** — the line breaks, the spacing, which
letters were embedded at all. That cannot be changed without re-typesetting the
document, which would change what the reader sees. So it is a floor, not an
unfinished job, and we say so rather than rounding the result up.

**The full rebuild does not fix it either — and we proved that against ourselves.**
Flattening every page to a picture makes the file entirely our own output, so it
*looks* like a clean win on paper. Publishing that would have been an overclaim,
so we attacked the resulting pictures instead: a program that studies only the
shape of the ink identifies which of the five produced the page **every single
time**. We then tried the obvious escape — rendering coarser — down to a
resolution where the body text is **unreadable**, and it still identified the
producer every time, because the signal is in the *margins and the width of the
text block*, not in fine detail. There is no setting at which the document is
still useful and the typesetter is anonymous. The full rebuild also **destroys
selectable text**: the page can no longer be searched, copied from, or read by a
screen reader — a real cost, stated before the mode is offered.

Three things we do **not** fix, and say so plainly: text hidden under a black box
is still in the file — we **warn about it and never silently repair it**, and a
test makes sure the warning stays necessary; embedded fonts keep their own small
print about who made the font; and the deep clean makes files about a **third
larger**, which matters because file size is itself one of the clues we report as
leaking.
<!-- FORMAT:pdf:END -->
