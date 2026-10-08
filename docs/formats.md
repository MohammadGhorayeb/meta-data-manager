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

**The `<id>` must be the id the format's Pareto matrix is published under** — the
`format` field written by `tests/scrub/gen_matrix_*.py`, which is also the
filename stem in `tests/harness/results/`. Word documents were listed in the
report's roadmap as `ooxml` (the name of the *shared package layer* in
`src/scrub/formats/ooxml/`) while their matrix was published as `docx`, and the
mismatch silently cost the report its entire Word section for a phase — the
lookup missed, the loader skipped it, and the guard that should have caught it
was comparing two lists that were both missing DOCX. The guard now reads the
matrices off disk, so a name that does not line up fails a test instead.

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

<!-- FORMAT:docx:BEGIN -->
**All three modes exist**, and every tick and cross beside them was measured on
four programs writing the same document — LibreOffice, macOS `textutil`, MAT2's
own output, and a synthetic writer built to differ on purpose.

A Word file is not a document. It is a **zip archive of small files**, so it has
two makers rather than one: the program that wrote the words, and the program
that zipped them up. They leak separately, so we report them separately rather
than averaging them into one number that would hide whichever half is worse.

**What the light clean removes.** The author, the company, the editing time, the
revision count, the template it came from, the little picture of the first page —
and the **hidden identifiers Word stamps on every paragraph**. Those last ones
matter more than they sound: a paragraph's id **travels with it when it is pasted
into another document**, so two files that look unrelated can be tied back to one
source. There is also a permanent id for the document itself, written twice in
two different dialects. We remove all of them. And the zipping half is closed
outright at this level: which computer zipped it, in what order, with what clock
— none of that separates one program from another in our output any more.

**A claim we had to correct.** This project inherited, from published research,
that Word's hidden editing-session ids defeat every cleaning tool including the
standard one. We measured it instead of repeating it, and **it is not true** — the
standard tool removes the ids the research named. What it leaves is the family
nobody names: the per-paragraph ids and the permanent document id above. So the
honest version is sharper than the original claim, not weaker: a cleaner can
close the channel that has a name and leave the one that does not.

**What the deep clean adds.** With the tags gone, what still identifies the
writing program is *how it spells the markup* — which shorthand names it uses for
its vocabulary, whether it writes an empty tag one way or the other, how it
punctuates the first line of each internal file. The deep clean rewrites every
internal file through one single writer, and that half stops separating anyone.
The document itself is untouched: all four programs' files render
**byte-identically**, before and after, checked by rendering them to pictures
rather than by trusting our own rule.

**What the full rebuild adds, and this is the one file type where it earns its
place.** For a PDF, the strongest mode photographs the page, which just moves the
typesetter's signature into the pixels. A Word document is different: rebuilding
it **re-types the document from scratch** through one program, so the program's
own choices replace the original's. Seven separate clues collapse to **one**.

**What is deliberately not fixed.**

- **The deep clean cannot remove what a program's *choices* say about it** —
  which internal files it bothers to write, which styles it defines, what it
  records in its settings, how it builds a paragraph. Changing any of those
  changes the document. It is a floor, not an unfinished job, and we say which
  clues are left rather than reporting a bare failure.
- **One clue survives even the full rebuild:** a document whose *original*
  defined a style keeps that style. It describes where the document came from,
  not which program handed it to us — the same kind of leftover as a re-recorded
  song remembering its first recording's quality setting.
- **The full rebuild's cost is reported as unmeasured, not as zero.** Our check
  renders the document before and after and gets identical pictures — but the
  program doing the rendering is the same one that rebuilt it, which is a program
  grading its own homework. The real cost shows up on opening the file in Word,
  and **Word cannot be driven by a script on any platform**. For the same reason
  Word is **not in the comparison set** at all, and the result says so out loud
  instead of quietly comparing three programs and calling it four.
- **Tracked changes and comments are refused by the light clean and resolved by
  the deeper two.** With markup switched on a reader *sees* them, so they are
  content, and deleting them silently would change what the document says. The
  light clean therefore stops and explains rather than touching such a file; the
  deeper modes accept the changes as the author intended and tell you so. The
  review history cannot be recovered afterwards.
- **Bookmark names and links stay.** A table of contents, a cross-reference and a
  hyperlink all find their destination *by name*, so deleting the name turns a
  working document into a broken one. Anything that survives for this reason is
  **listed back to you by name**. The one exception we do remove is the invisible
  marker recording where the cursor was when the file was last saved, because
  nothing points at it.
- **File size still separates the programs**, at every mode. It is not a tag and
  there is nothing inside the file to delete to fix it.
<!-- FORMAT:docx:END -->

<!-- FORMAT:mp4:BEGIN -->
Video, and the file type where the standard tool can hand a file back **unchanged**
and call it clean.

**What the light clean removes.** Where the video was shot, the make and model of
whatever shot it, the title and comments, the name of the program that encoded it,
and the date and time — in all *ten* places a two-track file writes them. It also
removes two things no ordinary tool touches, and they are the reason this format
was worth doing properly.

**The same coordinate, written three times.** Ask a program to put a location in a
video once, and it writes that coordinate into a dedicated location box *and*, in
some modes, three more times into the tag list under three different names. A
cleaner that knows about the tag list and not the location box leaves your position
sitting in the file. We measured all of them and remove all of them.

**The label that says which software made the file.** Every track carries a short
name for itself. Apple's software writes `Core Media Video`; the common open-source
tool writes `VideoHandler`. It is not a tag — it is a structural field — so
tag-oriented tools walk straight past it. **We measured what the standard tool does
with an Apple-made video: it reports the file "unchanged" and hands back a
byte-identical copy**, still carrying that label and still carrying the
wall-clock second the file was written. A user who runs it and is told nothing
changed has been told the file was already clean.

**The iPhone keeps a second copy where no player looks.** Every iPhone video we
measured writes its location, phone model, software version and recording time a
second time — once every ten seconds of filming, into a part of the file no video
player reads; shorter clips put it at the very end instead. The standard tool's clean
leaves that copy in place **and then reads its own output as having no location at
all**. So we do not delete the fields we know about and copy the rest: we rebuild the
video data from only what the player actually uses, and the leftover copy is never
written. An iPhone video also carries six hidden tracks beside the picture and sound
— among them, frame by frame, where each detected face is and an ID that follows it
— and those go too.

**What it costs you: nothing.** The picture and the sound are copied through
untouched, and we check that by *decoding* the result and comparing frames, not by
checking the file still opens. That distinction is not pedantry — it is the only
reason we found a bug of our own that had been shipping for two phases, where a
cleaned audio file kept perfect sound, opened correctly, reported the right length,
and played as static because the pointers into it were eight bytes out.

**What it does not do yet.** A cleaned video still shows **which program wrote the
container** — not which camera, not which person, and none of your content. Four
things give it away and we name them rather than rounding the result up: the
four-letter brand the program stamps at the front, the list of standards it claims
compatibility with, the order it writes the file's major sections in, and whether it
puts the index before or after the video data so the file can start playing before
it finishes downloading. Every one of those is a choice about *structure*, not a
piece of hidden data, so a mode that only deletes cannot touch them.

Measured against four producers, one of which is a genuinely different program
rather than another setting of the same one. Five other clues that *did* separate
them — the padding, which sections exist, the track labels, the width of one length
field, and whether timestamps were written at all — are **closed**. The four that
remain are the exact specification for the deeper mode, which would rewrite the
container through one single writer so every file comes out looking the same. That
mode is not built, and its row says *not tested* rather than guessing.

Those four producers are desktop programs; how far apart different phones and apps
are after cleaning has not been compared yet. The deeper mode would also renumber the
tracks that remain, because the file's next-track number still counts the hidden
tracks that were removed.

**Some encoders sign the video itself.** WhatsApp's writes its name, version and every
setting inside the coded picture. The light clean keeps the picture bit for bit, so
that signature stays — and the report says so by name rather than calling the file
clean.

**File size also still separates producers**, as it does for every format here: a
video encoded at a higher quality is a bigger file, and nothing in the metadata can
change that.
<!-- FORMAT:mp4:END -->

<!-- FORMAT:tiff:BEGIN -->
TIFF pictures — what scanners, publishing tools and photo editors save when quality
matters more than size, and what macOS makes when you convert a photo with its
built-in tools.

**What goes.** Everything the photo formats carry — camera and lens serials, owner,
dates, GPS, the editing software, XMP, IPTC and Photoshop blocks — plus two fields
TIFF has of its own that tools fill with **the file's original location on your
computer** ("DocumentName", "PageName"): the standard tool, ExifTool, leaves the first
of those in place even when told to remove everything. Every page of a multi-page
file is cleaned, and so is the small preview picture some TIFFs carry.

**A conversion can copy your details twice.** Converting a photo to TIFF with macOS's
own tool copied its camera, owner and location across, and added a second copy in a
different format it made up from the first. Both go.

**What stays.** Every page's pixels, exactly. The colour profile stays too, because
the colours depend on it: a standard one (sRGB, Display P3, Adobe RGB…) exactly as
it was, any other — a screen calibrated at home, say — with the details of who made
it removed. Nothing inside a TIFF can move, so removed details leave zeros of the
same length.
<!-- FORMAT:tiff:END -->

<!-- FORMAT:webp:BEGIN -->
WebP pictures — the format websites and phones increasingly save, still or animated.

**What goes.** The camera and personal details a photo carries (EXIF: owner,
serials, location, software) and its XMP copy; anything stored in a part of the file
that picture viewers skip over — they skip what they do not recognise, so it could
hold anything — including inside each frame of an animation; and anything stuck on
after the file's declared end. The flags announcing those parts are corrected so
the file stays valid.

**What stays.** The picture, exactly as it was compressed — every frame, every
transparency mask, the timing and the loop. A standard colour profile stays as it
was; any other keeps its colours and loses the details of who made it. Because the
compressed picture is copied as it is, the program that compressed it can still be
told apart from another — only through the file's size, in our measurement.
<!-- FORMAT:webp:END -->

<!-- FORMAT:gif:BEGIN -->
GIF pictures and animations.

**What goes.** Comments, the XMP block photo tools write into GIFs, any other
program's private block (viewers skip what they do not recognise, so it could hold
anything), and anything stuck on after the file's end.

**What stays.** Every frame exactly as it was compressed, its timing and
transparency, the loop, and text the GIF itself draws. A colour profile, if there is
one, follows the same rule as everywhere else: standard ones stay as they are,
others keep their colours and lose the details of who made them. Because the frames
are copied as compressed, the program that made the GIF can still be told apart
from another by how it laid them out.
<!-- FORMAT:gif:END -->

<!-- FORMAT:svg:BEGIN -->
SVG drawings — logos, icons, diagrams and charts, as drawing programs and websites
save them.

**What goes.** The drawing program's own notes: the file's name in the editor, the
program and its exact version, the folder a copy was exported to (which can name your
home folder), the "Created with…" and "Generator:" signatures, Illustrator's private
copy of its own document, author and date fields, and processing instructions meant
for an editor. **And the pictures inside the drawing:** a photo placed in an SVG is
often stored whole — LibreOffice stores the original photo byte for byte, with its
**GPS position**, camera serial and author, which the standard tool, ExifTool, does
not even report. Each picture inside is cleaned by its own format's rules and put
back.

**What stays.** Everything that draws, exactly as written — and we check that by
drawing it: every one of 4,252 SVGs shipped inside the apps on this machine looks
pixel-for-pixel the same after cleaning. The standard tool for this, MAT2, changed
how every one of the 14 such files we tried looks, and turned two into blank pictures.
References to files on your computer are kept (the drawing needs them to display) and
reported, so you can remove them in the editor.
<!-- FORMAT:svg:END -->

<!-- FORMAT:zip:BEGIN -->
ZIP archives — what "Compress" on a Mac, "Send to → Compressed folder" on Windows, and
`zip` on Linux make.

**What goes.** The archive's own records about the files: the time each was last
changed (in the clock of the machine that made it, so a time zone), the account's
user number on Unix, permission settings that show how that computer was set up,
comments. **The hidden folder a Mac adds** (`__MACOSX`), which we measured holding
**the web address each file was downloaded from**, the browser, and the download's
quarantine record — compressed, so nobody sees it by looking. Finder's folder-view
files (`.DS_Store`) and Windows' thumbnail caches (`Thumbs.db`, which can hold small
copies of pictures that are not even in the archive). **And every file inside is
cleaned by its own format's rules** — the photo's GPS, the Word document's author,
an archive inside the archive — and put back.

**What stays.** The names, the folders (empty ones too), each file's content, and
whether a script can be run. Plain text is kept exactly as written: it has no
hidden part, so what it says is the content. A file of a type we do not clean
stops the whole archive, naming the file, unless you ask to keep such files
untouched (`--keep-unknown-members`), and the report then lists each one.

**How it compares.** The standard tool, MAT2, crashed on an archive made with a Mac's
*Compress* and left an empty file behind; refused an archive holding a shell
script; and, told to keep the script, made it impossible to run, removed the empty
folder and made every file readable by its owner only. Four different archiving
programs given the same folder produce archives that tell them apart on eleven
points; after cleaning, the four are the same file, byte for byte.
<!-- FORMAT:zip:END -->

<!-- FORMAT:exe:BEGIN -->
Programs — the files a computer runs, on all three systems: Linux (ELF), Mac
(Mach-O, including the "universal" kind that carries an Intel and an Apple-chip copy
in one file) and Windows (PE: `.exe` and `.dll`).

**What a program says about whoever built it.** Compiling a program leaves notes in
it that the program itself never reads: the folder it was built in (which usually
contains your user name), the exact compiler and the exact version of the operating
system's package it came from, the full list of settings the compiler was given, and
the name of the source file. A program written in Go also records the **commit** it
was built from, with its **date and time**, and the project's address — often
`github.com/your-name/...`. We measured all of this on programs we built ourselves,
before writing any code.

**What goes.** Every part of the file the computer never loads when it runs the
program is wiped: the build notes, the debugging information, the source file names.
Go's commit, date and project address are wiped too — but only when the program has
no way of reading them itself. The program's "fingerprint" numbers (build IDs) are
recalculated from the cleaned file, so they look normal but no longer match the
original build. The standard tool for this, `strip`, removes the folder names but
keeps a fingerprint calculated *from* them: two people's stripped programs can still
be told apart. Ours cannot.

**Mac programs have one more lock.** On a Mac with an Apple chip, every program
carries a signature that covers every byte of it; change one byte without redoing
the signature and the Mac refuses to start it. So after cleaning we recompute that
signature ourselves — Apple's own checking tool accepts it — and the name the
signature records (often your source file's name) is replaced too. A Mac program
signed with a developer's *identity* is left alone: cleaning would break it on the
recipient's Mac, and that signature is meant to name its publisher.

**Windows programs carry a few things of their own.** The exact minute the program
was linked; a hidden block Microsoft's compiler writes listing every tool version
that touched the program, which researchers use to tell one developer's programs
from another's; the location of its debugging file, which on a real program we
checked — the launcher that ships inside every copy of Python's `pip` — names its
author's Windows user folder; and the company, copyright and original file name in
its properties. All of that goes, and the program's built-in checksum is redone. A
Windows program signed by its publisher is left alone, for the same reason as on the
Mac.

**What it costs you: nothing.** The test is that the program **runs the same** —
same output, same exit code — and that is checked by actually running real programs
built with gcc, clang, Swift, Rust, Go and the Windows compilers, before and after, on Linux, on a Mac, and under Wine (which runs Windows programs on Linux).

**What stays, and why.** Anything the program itself can print stays, because
changing it would change what the program does: if your program prints its own
source location when it crashes, that location is part of the program. We tell you
when we see one, and how to rebuild without it. And because nothing is moved, the
wiped areas keep their size, which says roughly how long the removed folder names
were.
<!-- FORMAT:exe:END -->

<!-- FORMAT:raw:BEGIN -->
The files a camera saves when you choose RAW — Canon, Nikon, Sony, Fujifilm, Olympus,
Panasonic, and Apple's ProRAW — and the format where nothing inside the file is
allowed to move.

**Why nothing can move.** A raw file is the camera's sensor reading, stored at
positions the file's own index records. The camera's private notes — where the
serial numbers are — sit in the same block as information the developing software
needs to get the colours right. Delete that block and a Nikon file stops opening and
a Canon one loses its colour balance. So we never delete or shift anything: we
overwrite exactly the bytes that identify you, with zeros, maker by maker.

**What goes.** Serial numbers of the camera body, the lens, the extender and the
flash; the owner's name and copyright the camera was set up with (one of our samples
carried an email address); the shot counter, which links photos to one camera even
without a serial; every date and time, including time zones — one Canon file named
the **city**; the GPS position, altitude and direction; the software version; and
the extra copy of all of this that some cameras put inside the preview picture,
including the iPhone's preview, which carries its **own copy of the location**.

**Copies the standard tool cannot see.** Three times while building this we cleaned
everything ExifTool could name, searched the file's raw bytes anyway, and found
something still there: an owner's name inside a Canon block ExifTool does not
decode, a Fujifilm serial and date inside Fujifilm's own header, and Canon's shot
counter in two places at a position that differs per camera. All are removed. That
is why our final check reads the bytes, not just the tags.

**What it costs you: nothing you can see.** The sensor data comes out **bit for bit
identical**, and the photo developed with the camera's own colour settings comes out
**pixel for pixel identical** — checked with an independent decoder on real files
from every maker. Previews stay, cleaned, so your photo viewer still shows a
thumbnail; we checked that the thumbnail macOS draws is unchanged.

**What stays, on purpose.** The camera make and model, and the lens model: the
software that develops a raw file uses them to pick the right colour profile, and
without them your photo would develop wrongly. They say which *kind* of camera took
the photo, never which one. Because nothing can move, each removed detail leaves
zeros of the same length behind, and a blanked time zone reads as UTC.

**What is not measured yet.** Whether a cleaned file can still be traced to one
particular camera *body* — beyond the sensor's own noise, which no metadata tool can
remove. That needs many photos from two cameras of the same model, which we do not
have yet, so we do not claim it either way.
<!-- FORMAT:raw:END -->

<!-- FORMAT:heic:BEGIN -->
The photos on your phone, and the first format where "delete the tag" is not
even half the job.

An iPhone photo is not one picture in a wrapper. It is a **table of parts**: the
photograph itself is a grid of sixty to ninety-five separate tiles, and stored
right beside them are the camera data, a small preview, and up to six **extra
pictures of the same scene** — depth maps, a brightness layer, and Apple's
machine-made maps of where the people and the sky are. Everything is located by
its exact position in the file, so removing anything means correctly re-pointing
everything that came after it.

**What the light clean removes:** the camera make and model, the iOS version,
the date, the GPS position, altitude and timestamp, every XMP block, the
embedded preview, and the extra pictures described above. Measured on six real
iPhone photos, the richest went from **428 pieces of metadata to 147**, and the
picture came out **pixel-for-pixel identical** — checked by decoding it with a
different program than the one we wrote.

**One thing it removes that nobody talks about.** Apple ships a block of about
58 KB — three to five percent of the file — describing the photo's own **subject**:
whether there are people in the frame, how much of it is skin, and a small map of
where they are. Not a fact about the camera but a judgement the phone made about
what the picture is *of*. It has never been a secret, but nobody looks. It goes.

**One thing it deliberately keeps:** the HDR brightness layer. Without it the
photo still opens and looks the same on an ordinary screen, but renders flat on a
phone or monitor that supports HDR. It says nothing about the subject — it is a
half-size brightness map of the very pixels we are preserving — so it stays, and
the report names it rather than leaving it unexplained.

**What it does not do yet.** A cleaned photo still shows which *software* wrote
the file — not which phone or which person, but the header stamp, the order of
the internal tables and the widths of their fields are all the writing program's
choices, and the only cleaning level this format has preserves data rather than
rewriting it. We measured that against three container writers wrapping identical
picture data, so the result is about the software and not the photo, and we
publish it as a **failure with each clue named** — because that list is exactly
the specification for the deeper level that would close it.
<!-- FORMAT:heic:END -->
