# Using scrubmeta

scrubmeta removes hidden information from files: GPS positions, serial numbers, author
names, edit histories, thumbnails of what a page used to say. It reads the file's real
format rather than trusting its extension, and it either cleans a file completely or
refuses and writes nothing.

## Install

You need Python 3.11 or newer and `pipx`, which installs a command-line program in a
folder of its own. If `pipx --version` says it is not found, install it first, then
open a new terminal:

| | macOS | Debian / Ubuntu |
|---|---|---|
| pipx | `brew install pipx && pipx ensurepath` | `sudo apt install pipx && pipx ensurepath` |

Then, from a copy of this repository:

```
git clone https://github.com/MohammadGhorayeb/meta-data-manager
cd meta-data-manager
pipx install .
scrubmeta doctor
```

`doctor` lists the helper programs it found, their versions, and which cleaning levels
each file type has on this machine (`F1` light, `F2` deep, `F3` full; `raw` is camera
RAW, `exe` programs, `metafile` the EMF/WMF drawings inside Word files). It exits 9
when something is missing. That is not a failure: the light clean of every file type
works with none of the helpers. The rest need:

| program (`doctor` name) | needed for | macOS | Debian / Ubuntu |
|---|---|---|---|
| ExifTool (`exiftool`) | the independent check after each clean | `brew install exiftool` | `apt install libimage-exiftool-perl` |
| jpegtran | JPEG deep clean | `brew install jpeg-turbo` | `apt install libjpeg-turbo-progs` |
| ffmpeg | audio deep and full cleans | `brew install ffmpeg` | `apt install ffmpeg` |
| lame | MP3 full clean | `brew install lame` | `apt install lame` |
| poppler (`pdftotext`, `pdftoppm`) | PDF deep and full cleans | `brew install poppler` | `apt install poppler-utils` |
| LibreOffice (`soffice`) | Word full clean | from libreoffice.org | `apt install libreoffice-writer` |

**Or use the container**, which has all of them at one exact version. Everyone using it
on the same kind of processor produces the same bytes, which matters for the full clean
(see *What it cannot do*):

```
docker build -t scrubmeta .
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work scrubmeta photo.jpg clean.jpg
```

Build it from the repository folder. The container then sees only the folder you run it
from, so `cd` to your files first and give paths inside that folder. Everything after
`scrubmeta` works as below; `doctor` too.

## Clean a file

```
scrubmeta photo.jpg clean.jpg
```

The original is left alone, and the output may not be the same file as the input. It
prints what it found and what happened to each field, then what ExifTool, a separate
program, reads in the result:

```
JPEG · F1 · 951 B → 677 B
  - EXIF:Artist            Jane Doe
  - EXIF:DateTimeOriginal  2020:01:01 12:00:00
  - EXIF:GPSLatitudeRef    N
  - EXIF:GPSLatitude       51° 30' 0.00"
  - EXIF:other tags        10 more
  = kept: JFIF header
  5 removed, 1 kept

  Checked with exiftool 13.55 — a different implementation, not us:
    16 metadata tags before → 4 after
    gone entirely: ExifIFD, GPS, IFD0
    still reported: JFIF (4)
    none of the removed values appear where they were removed from
  Check it yourself:
    exiftool -a -G1 -s /Users/you/clean.jpg
```

Lines starting `-` were removed, `~` changed, `+` added, `=` kept. *Still reported* is what
ExifTool can read in the cleaned file, here the basic JPEG header every JPEG has. If
ExifTool can still read a value the tool says it removed, the check says so loudly:
treat that file as not cleaned, and please report it.

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
[results table](../README.md#results-measured-not-assumed). Run the full clean once:
running it again on its own output loses quality a second time.

## Clean a whole folder

```
scrubmeta my-folder cleaned-folder
```

The output must be new or empty, and neither folder may be inside the other. Everything is cleaned
into the same place under the output, and `scrub-results.csv` there has one row per
file (`scrub-results-2.csv` if your folder already holds a file of that name). It
names the fields removed, never their values, so it can travel with the files. Its
columns: the file's path, the outcome below, its type and level, the size before and
after, how many fields were removed and which ones (`removed_fields`, `changed_fields`,
`kept_fields`), what ExifTool found (`independent_check`), and `notes`.

| outcome | meaning |
|---|---|
| cleaned | written, cleaned |
| kept as text | plain text has no hidden part; what it says is its content |
| refused | could not be cleaned safely, or ExifTool still read a removed value; not written; `notes` or `independent_check` says why |
| not supported | a type it does not read; not written |
| dropped | a system file (`.DS_Store`, `._*`, `Thumbs.db`), never your own |
| kept unscrubbed | only with `--keep-unknown-members`: copied untouched, **not cleaned** |
| skipped | a symbolic link or other non-file; links are never followed |
| error | an unexpected failure; not written; please report it |

One file that fails never stops the run. `--jobs N` cleans N files at once (`--jobs 0`:
one per CPU). The run exits 8 if it finished but some files were not written: anything
but *cleaned*, *kept* or *dropped*, a skipped link included. In folder mode
`--no-report` hides only the per-file lines; the ExifTool check still runs, because
here it removes any file it contradicts. Only `--no-verify` turns it off.

## When it refuses

Nothing is written. The exit code says what kind of refusal it is and the message says
which one, so **read the message first**: if it names one of the cases in the list
below the table, the next step is yours to choose, whatever the code.

| exit | meaning | what to do |
|---|---|---|
| 0 | cleaned | |
| 2 | usage: a missing input, a wrong option, or an output that is the input itself or a folder | fix the command |
| 3 | a type it does not read, or a kind of that type it refuses (a .NET or signed program) | see [Files we refuse to clean](limits.md#files-we-refuse-to-clean) |
| 4 | the file is damaged, or built in a way it cannot account for | if the message names a case below, see it; otherwise open and re-save the file in its own program |
| 5 | that level is not offered for this type | use a level from the table above |
| 6 | cleaning would have changed the content, or left something behind | if the message names a case below (Word's tracked changes), see it; otherwise please report it |
| 7 | not enough memory, or more pixels than that level can decode | close programs, use a lighter level, or `--skip-memory-check` |
| 8 | folder mode: finished, but not every file was written | read `scrub-results.csv` |
| 9 | a helper program is missing, refused the file, or ran too long | `scrubmeta doctor` |
| 1 | something nobody anticipated | please report it |

Some refusals are about what the file contains, and you decide what happens next:

- **Word with tracked changes or comments**: the light clean refuses (exit 6), because
  they hold reviewers' names and earlier wording. Accept or reject them first, or use
  the deep clean, which accepts the changes and removes the comments.
- **Word with something embedded**, such as a chart's spreadsheet: refused for now.
- **Excel with comments, links to other workbooks, pivot tables or database
  connections**: remove them in Excel first. A pivot table can keep a full copy of its
  source data, even data deleted from every sheet.
- **PowerPoint with comments, embedded fonts, or a chart whose spreadsheet is stored
  inside it**: remove them in PowerPoint first. That spreadsheet is a whole Excel file
  with its own author.
- **Signed or locked files**: PDFs and programs signed by their maker, encrypted PDFs,
  copy-protected e-books and videos. Cleaning would break the signature, or needs a
  key. Decrypt an encrypted PDF first if you can.
- **An archive or folder holding a type it does not read**: `--keep-unknown-members`
  copies those files untouched, **not cleaned**, and the report lists them.
- **A video with subtitles**: `--drop-subtitles` drops those tracks. A drone's GPS is
  stored the same way a film's subtitles are, so it will not guess.

Every refusal is listed in plain words in
[Files we refuse to clean](limits.md#files-we-refuse-to-clean). The rest of
[`limits.md`](limits.md) has the measured detail behind each one.

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
- **A clean that is killed** (the machine crashes, or you force-quit it) can leave a
  copy of the original in a private work folder only your account can open. The next
  run removes it.
- **The full clean can show which versions of its helper programs ran**, as a group of
  software versions rather than a person. The container avoids that: everyone using it
  has the same versions. For MP3, M4A and PDF the full clean also differs between Intel
  and ARM processors (Apple's chips), even in the container.

## All options

| option | effect |
|---|---|
| `--fidelity F1\|F2\|F3` | the cleaning level (default `F1`) |
| `--no-report` | single file: print no report and skip the ExifTool check; folder: hide the per-file lines only |
| `--no-verify` | skip the ExifTool check (a fraction of a second per file); in folder mode this also stops it removing files it contradicts |
| `--jobs N` | folder mode: N files at once; `0` is one per CPU |
| `--keep-unknown-members` | archives and folders: copy types it cannot read, **unscrubbed** |
| `--drop-subtitles` | video: drop subtitle and caption tracks instead of refusing |
| `--skip-memory-check` | try even if the machine seems short of memory; it still never writes a partial file |
| `--version` | print the version |
| `--help` | print the options |
| `doctor` | as the only argument: what this install can do |
