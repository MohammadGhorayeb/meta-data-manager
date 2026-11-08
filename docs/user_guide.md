# Using scrubmeta

scrubmeta removes hidden information from files: GPS positions, serial numbers, author
names, edit histories, thumbnails of what a page used to say. It reads the file's real
format rather than trusting its extension, and it either cleans a file completely or
refuses and writes nothing.

## Install

From a copy of this repository:

```
pipx install .
scrubmeta doctor
```

`doctor` lists the helper programs it found, their versions, and which cleaning levels
they enable. The light clean of every file type works with none of them. The rest:

| program | needed for | macOS | Debian / Ubuntu |
|---|---|---|---|
| ExifTool | the independent check after each clean | `brew install exiftool` | `apt install libimage-exiftool-perl` |
| jpegtran | JPEG deep clean | `brew install jpeg-turbo` | `apt install libjpeg-turbo-progs` |
| ffmpeg | audio deep and full cleans | `brew install ffmpeg` | `apt install ffmpeg` |
| lame | MP3 full clean | `brew install lame` | `apt install lame` |
| poppler | PDF deep and full cleans | `brew install poppler` | `apt install poppler-utils` |
| LibreOffice | Word full clean | from libreoffice.org | `apt install libreoffice-writer` |

**Or use the container**, which has all of them at one exact version. Everyone using it
produces the same bytes, which matters for the full clean (see *What it cannot do*):

```
docker build -t scrubmeta .
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work scrubmeta photo.jpg clean.jpg
```

## Clean a file

```
scrubmeta photo.jpg clean.jpg
```

The original is left alone, and the output may not be the same file as the input. It
prints what it found and what happened to each field, then what ExifTool, a separate
program, reads in the result:

```
JPEG · F1 · 2.8 KB → 1.5 KB
  - EXIF:GPSLatitude       51° 30' 0.00"
  - EXIF:DateTimeOriginal  2020:01:01 12:00:00
  = kept: JFIF header
  Checked with exiftool 13.55 — a different implementation, not us:
    none of the removed values appear where they were removed from
```

That report shows the values it removed, which is what makes it useful on your screen
and a disclosure if it lands in a log. `--no-report` turns it off.

## The three levels

| level | what it does | what it costs | |
|---|---|---|---|
| **Light** (default, `F1`) | Removes the hidden information and changes nothing else | Nothing: the picture, sound or text is untouched | every type |
| **Deep** (`--fidelity F2`) | Also rebuilds how the file is packed, losslessly | Nothing you can see or hear | JPEG, PNG, FLAC, M4A, PDF, Word |
| **Full** (`--fidelity F3`) | Re-encodes the content in one standard way | A small quality loss, like any re-save | JPEG, MP3, M4A, PDF, Word |

Use **light** to remove your name, location and history. The stronger levels also hide
*which program or device made the file*, which the light clean leaves visible in how
the file is built. For the exact result per type, see the
[results table](../README.md#results-measured-not-assumed).

## Clean a whole folder

```
scrubmeta my-folder cleaned-folder
```

The output must be new or empty, and may not be inside the input. Everything is cleaned
into the same place under the output, and `scrub-results.csv` there has one row per
file. It names the fields removed, never their values, so it can travel with the files.

| outcome | meaning |
|---|---|
| cleaned | written, cleaned |
| kept as text | plain text has no hidden part; what it says is its content |
| refused | could not be cleaned safely; not written; `notes` says why |
| not supported | a type it does not read; not written |
| dropped | a system file (`.DS_Store`, `._*`, `Thumbs.db`), never your own |
| kept unscrubbed | only with `--keep-unknown-members`: copied untouched, **not cleaned** |
| skipped | a symbolic link or other non-file; links are never followed |
| error | an unexpected failure; not written |

`--jobs N` cleans N files at once (`--jobs 0`: one per CPU). The run exits 8 if it
finished but some files were not written.

## When it refuses

Nothing is written, and the message gives the reason.

| exit | meaning | what to do |
|---|---|---|
| 0 | cleaned | |
| 2 | usage, or an output that is the input itself or a folder | fix the command |
| 3 | a type it does not read | see `docs/limits.md` #8 |
| 4 | the file is damaged, or built in a way it cannot account for | open and re-save it in its own program |
| 5 | that level is not offered for this type | use a level from the table above |
| 6 | cleaning would have changed the content, or left something behind | please report it |
| 7 | not enough memory, or more pixels than that level can decode | close programs, use a lighter level, or `--skip-memory-check` |
| 8 | folder mode: finished, but not every file was written | read `scrub-results.csv` |
| 9 | a helper program is missing, refused the file, or ran too long | `scrubmeta doctor` |
| 1 | something nobody anticipated | please report it |

Some refusals are about what the file contains, and you decide what happens next:

- **Word with tracked changes or comments**: the light clean refuses, because they hold
  reviewers' names and earlier wording. Accept or reject them first, or use the deep
  clean, which accepts the changes and removes the comments.
- **Excel with comments, pivot caches or links to other workbooks; PowerPoint with
  comments**: resolve them in the program first.
- **Word or PowerPoint with an embedded spreadsheet**: refused for now.
- **Signed or encrypted PDFs, signed programs**: cleaning would break the signature,
  or needs a key.
- **An archive holding a type it does not read**: `--keep-unknown-members` copies those
  members untouched and the report lists them.
- **A video with subtitles**: `--drop-subtitles` drops those tracks. A drone's GPS is
  stored the same way a film's subtitles are, so it will not guess.

The whole list is in [`limits.md`](limits.md).

## What it cannot do

- **The original stays where it was.** The tool never deletes it. If the point is that
  nobody finds the original, deleting it (and its backups) is up to you.
- **What the content itself shows.** A face, a street sign, a name typed in the
  document, words hidden under a black box in a PDF. It warns about the last when it
  can see it, but it does not edit what you wrote.
- **Traces in the content.** Every camera sensor leaves a faint pattern in its photos,
  and every microphone and room shapes a recording. No tool can remove those without
  destroying the picture or the sound.
- **A cleaned file looks cleaned.** Someone can tell a tool was used, just not what was
  removed.
- **The file's name, and what your computer records about it**: its date on disk, its
  folder, your system's own logs. Those are not in the file. On a Mac, every new file
  also gets a provenance tag from macOS, beside the file and not in it.
- **The full clean can show which versions of its helper programs ran**, as a group of
  software versions rather than a person. The container avoids that: everyone using it
  has the same versions.

## All options

| option | effect |
|---|---|
| `--fidelity F1\|F2\|F3` | the cleaning level (default `F1`) |
| `--no-report` | do not print the before/after report |
| `--no-verify` | skip the ExifTool check (a fraction of a second per file) |
| `--jobs N` | folder mode: N files at once; `0` is one per CPU |
| `--keep-unknown-members` | archives and folders: copy types it cannot read, **unscrubbed** |
| `--drop-subtitles` | video: drop subtitle and caption tracks instead of refusing |
| `--skip-memory-check` | try even if the machine seems short of memory; it still never writes a partial file |
| `--version` | print the version |
| `doctor` | as the only argument: what this install can do |
