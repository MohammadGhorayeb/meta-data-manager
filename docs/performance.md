# Speed and memory, measured

November plan N3 ([`november_plan.md`](november_plan.md)). Until November nothing about
speed was measured, and memory only for video (limit #38). `scripts/perf_bench.py`
builds realistic files of each type at 1, 10 and 100 MB (1 GB for video and ZIP),
cleans each at every level the type offers in a **fresh process**, and records the
time, our own peak memory and the external programs' peak. Reproduce it with:

```
./.venv/bin/python scripts/perf_bench.py --sizes 1 10 100 --big 1000 --heavy-max 10
```

## What the first measurement found, and what changed

Each pair was measured before and after on the same machine and the same files.

| clean | before | after | what it was |
|---|---|---|---|
| Video light clean, 1 GB | 4.1 s, **2.07 GB** | 2.9 s, **44 MB** | the whole file held twice; now streamed from a mapped input to the output |
| Excel light clean, 1 MB | 4.5 s, 249 MB (254x) | 1.1 s, 30 MB (30x) | every sheet parsed into an object per tag, three times -- to check it was well-formed, find the strings in use, count hidden rows; now streamed |
| MP3 full clean, 10 MB | 27.5 s, 1.17 GB (117x) | 7.0 s, 129 MB (13x) | the audio check decoded and filtered the whole recording to compare its first 4.5 s |
| M4A full clean, 8.7 MB | 25.4 s, 934 MB (107x) | 4.7 s, 123 MB (14x) | the same check |
| M4A full clean, 8 min of noisy audio | 1.4 GB | 183 MB | the check's fallback filtered the whole recording in one FFT; now in blocks, scores unchanged to 1e-5 |
| JPEG light clean, 89 MB | 16.1 s | 0.3 s | the compressed picture scanned one byte at a time in Python, six to eight times a clean |
| ZIP of photos, 18 MB | 9-11x the file | 5.5x | every member's compressed bytes copied out of the archive and held |
| Folder of 300 photos, with the ExifTool check | 48.5 s | **1.8 s**; 0.9 s with `--jobs 4` | ExifTool started twice a file; now one kept running per process |

Memory checks that priced files too low, or not at all:

- **ZIP** assumed 3x (archive + contents) and measured up to 3.2x, so a file the check
  passed could need more. Now 3.5x.
- **Word, Excel and PowerPoint** are priced from their declared part sizes since N2
  (XML at 28x, measured on Word; Excel now measures 6x its XML).
- **The levels that decode the content** were priced at their format's light clean,
  or not at all -- and a multiple of the file's size is the wrong unit for them. A
  decoded picture costs its pixels (JPEG 18-21 bytes a pixel at the deep clean, 27-32
  at the full clean, PNG 25-35, on flat and noisy pictures alike, while the same
  picture's cost per FILE byte ranged from 13x to 8,400x). Decoded sound costs its
  length (about 30 MB + 0.32 MB a second, the same at 64 and 192 kbps). JPEG, PNG, MP3
  and M4A now estimate their need from the file's own header (`memory_need`), and
  `test_memory_preflight.py` re-measures it. PDF keeps a factor per level (7x, full 8x).

## The table

Measured 2026-11-08 on arm64 Darwin 26.6.2, 10 CPUs, Python 3.14.5.

Our peak is memory that cannot be given back (macOS physical footprint; Linux anonymous memory, sampled). Resident also counts mapped file pages, which the system reclaims at will: they make streamed video look large.

| type | level | file MB | clean s | MB/s | our peak MB | x file | resident MB | external peak MB | ExifTool check s |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| jpeg | F1 | 0.9 | 0.01 | 178 | 2.8 | 3.1 | 2.8 | 23 | 0.16 |
| jpeg | F2 | 0.9 | 0.09 | 10 | 43 | 46.5 | 44 | 23 | 0.15 |
| jpeg | F3 | 0.9 | 0.11 | 9 | 63 | 67.5 | 64 | 23 | 0.15 |
| jpeg | F1 | 9.0 | 0.03 | 261 | 36 | 4.0 | 36 | 23 | 0.15 |
| jpeg | F2 | 9.0 | 0.68 | 13 | 407 | 45.3 | 407 | 68 | 0.15 |
| jpeg | F3 | 9.0 | 0.91 | 10 | 597 | 66.5 | 598 | 23 | 0.15 |
| jpeg | F1 | 89 | 0.32 | 282 | 267 | 3.0 | 267 | 23 | 0.16 |
| jpeg | F2 | 89 | refused: src.scrub.errors.ResourceError: JPEG F2: the picture declares 17136 x 12848 pixels, more than the 178,956,970 this level can decode; the light clean (F1) does n | | | | | | |
| jpeg | F3 | 89 | not run (heavy level, over 10 MB) | | | | | | |
| png | F1 | 1.0 | 0.00 | 317 | 4.2 | 4.3 | 4.2 | 24 | 0.15 |
| png | F2 | 1.0 | 0.08 | 12 | 15 | 15.1 | 15 | 24 | 0.15 |
| png | F1 | 9.9 | 0.02 | 478 | 44 | 4.4 | 44 | 24 | 0.16 |
| png | F2 | 9.9 | 0.62 | 16 | 154 | 15.6 | 155 | 24 | 0.15 |
| png | F1 | 99 | 0.19 | 527 | 424 | 4.3 | 423 | 24 | 0.18 |
| png | F2 | 99 | 5.90 | 17 | 1427 | 14.4 | 1427 | 24 | 0.18 |
| tiff | F1 | 1.0 | 0.01 | 159 | 4.0 | 4.1 | 4.0 | 23 | 0.15 |
| tiff | F1 | 10.0 | 0.05 | 201 | 40 | 4.0 | 40 | 23 | 0.15 |
| tiff | F1 | 100 | 0.51 | 197 | 400 | 4.0 | 400 | 23 | 0.15 |
| mp3 | F1 | 1.0 | 0.01 | 118 | 2.4 | 2.4 | 2.4 | 24 | 0.15 |
| mp3 | F3 | 1.0 | 1.01 | 1 | 38 | 38.5 | 40 | 24 | 0.15 |
| mp3 | F1 | 10.0 | 0.08 | 124 | 24 | 2.4 | 24 | 24 | 0.15 |
| mp3 | F3 | 10.0 | 7.04 | 1 | 129 | 12.9 | 169 | 24 | 0.16 |
| mp3 | F1 | 100 | 0.88 | 113 | 245 | 2.5 | 245 | 24 | 0.15 |
| mp3 | F3 | 100 | not run (heavy level, over 10 MB) | | | | | | |
| flac | F1 | 0.9 | 0.00 | 457 | 2.7 | 3.0 | 2.7 | 24 | 0.15 |
| flac | F2 | 0.9 | 0.06 | 16 | 3.6 | 4.0 | 3.6 | 26 | 0.15 |
| flac | F1 | 9.6 | 0.01 | 1014 | 29 | 3.0 | 29 | 24 | 0.16 |
| flac | F2 | 9.6 | 0.26 | 37 | 39 | 4.0 | 39 | 26 | 0.15 |
| flac | F1 | 97 | 0.08 | 1273 | 291 | 3.0 | 291 | 24 | 0.16 |
| flac | F2 | 97 | not run (heavy level, over 10 MB) | | | | | | |
| m4a | F1 | 0.9 | 0.00 | 384 | 3.5 | 4.1 | 3.5 | 28 | 0.18 |
| m4a | F2 | 0.9 | 0.16 | 5 | 18 | 20.6 | 18 | 28 | 0.17 |
| m4a | F3 | 0.9 | 0.77 | 1 | 35 | 41.0 | 38 | 28 | 0.18 |
| m4a | F1 | 8.7 | 0.01 | 973 | 35 | 4.0 | 35 | 28 | 0.19 |
| m4a | F2 | 8.7 | 0.75 | 12 | 172 | 19.9 | 172 | 28 | 0.18 |
| m4a | F3 | 8.7 | 4.73 | 2 | 123 | 14.3 | 150 | 28 | 0.18 |
| m4a | F1 | 87 | 0.07 | 1251 | 349 | 4.0 | 349 | 31 | 0.18 |
| m4a | F2 | 87 | not run (heavy level, over 10 MB) | | | | | | |
| m4a | F3 | 87 | not run (heavy level, over 10 MB) | | | | | | |
| mp4 | F1 | 1.3 | 0.03 | 45 | 1.4 | 1.0 | 4.0 | 28 | 0.18 |
| mp4 | F1 | 11 | 0.06 | 182 | 4.7 | 0.4 | 22 | 28 | 0.18 |
| mp4 | F1 | 102 | 0.28 | 368 | 8.6 | 0.1 | 208 | 29 | 0.18 |
| mp4 | F1 | 1010 | 2.86 | 353 | 44 | 0.0 | 2047 | 32 | 0.18 |
| pdf | F1 | 0.9 | 0.12 | 8 | 5.6 | 5.9 | 6.2 | 24 | 0.16 |
| pdf | F2 | 0.9 | 0.17 | 6 | 5.1 | 5.4 | 5.8 | 24 | 0.15 |
| pdf | F3 | 0.9 | 0.67 | 1 | 26 | 27.7 | 28 | 31 | 0.15 |
| pdf | F1 | 9.5 | 1.15 | 8 | 59 | 6.2 | 60 | 24 | 0.17 |
| pdf | F2 | 9.5 | 1.21 | 8 | 45 | 4.8 | 46 | 24 | 0.16 |
| pdf | F3 | 9.5 | 6.61 | 1 | 66 | 7.0 | 68 | 32 | 0.18 |
| pdf | F1 | 94 | 11.68 | 8 | 584 | 6.2 | 584 | 25 | 0.27 |
| pdf | F2 | 94 | not run (heavy level, over 10 MB) | | | | | | |
| pdf | F3 | 94 | not run (heavy level, over 10 MB) | | | | | | |
| docx | F1 | 0.9 | 0.04 | 23 | 5.2 | 5.6 | 5.3 | 31 | 0.22 |
| docx | F2 | 0.9 | 0.04 | 21 | 6.6 | 7.0 | 6.7 | 31 | 0.22 |
| docx | F3 | 0.9 | 3.24 | 0 | 9.9 | 10.6 | 10 | 301 | 0.23 |
| docx | F1 | 9.4 | 0.36 | 26 | 53 | 5.6 | 53 | 32 | 0.23 |
| docx | F2 | 9.4 | 0.37 | 25 | 57 | 6.1 | 58 | 32 | 0.24 |
| docx | F3 | 9.4 | 3.40 | 3 | 66 | 7.0 | 66 | 378 | 0.24 |
| docx | F1 | 94 | 3.60 | 26 | 487 | 5.2 | 487 | 34 | 0.34 |
| docx | F2 | 94 | not run (heavy level, over 10 MB) | | | | | | |
| docx | F3 | 94 | not run (heavy level, over 10 MB) | | | | | | |
| pptx | F1 | 1.0 | 0.10 | 10 | 7.6 | 7.8 | 7.7 | 32 | 0.23 |
| pptx | F1 | 9.5 | 0.47 | 20 | 62 | 6.6 | 62 | 32 | 0.29 |
| pptx | F1 | 94 | 4.06 | 23 | 494 | 5.3 | 494 | 38 | 0.57 |
| xlsx | F1 | 1.0 | 1.06 | 1 | 30 | 30.3 | 30 | 32 | 0.23 |
| xlsx | F1 | 9.7 | 11.57 | 1 | 285 | 29.2 | 285 | 31 | 0.42 |
| zip | F1 | 1.8 | 0.09 | 20 | 9.9 | 5.5 | 10.0 | 31 | 0.22 |
| zip | F1 | 9.1 | 0.44 | 21 | 57 | 6.3 | 57 | 31 | 0.23 |
| zip | F1 | 90 | 4.36 | 21 | 464 | 5.1 | 464 | 32 | 0.25 |
| zip | F1 | 903 | 43.25 | 21 | 4585 | 5.1 | 4582 | 37 | 0.54 |

Excel was measured separately, at 1 and 10 MB (its 100 MB file holds ~500 MB of XML).
"Not run" marks the levels that hand the whole file to another program -- a re-encode
or a re-typeset -- above 10 MB; "refused" is a deliberate refusal (limit #84).

## Reading it

- **The light cleans are fast**: images, audio and video at 100-1,300 MB/s. Their
  memory is about 2.5-4x the file -- the input and the output (video: not even that).
- **The levels that decode the content cost what decoding costs**: a JPEG decoded to
  pixels is 45-67x its compressed size, PNG 15x, audio 13-24x (16-bit samples, two
  recordings), and they run at 1-17 MB/s. The memory check prices each level apart.
- **Documents** clean at 8-26 MB/s; a 94 MB PDF or Word file needs about 6x in memory.
- **ExifTool's check** costs 0.15-0.6 s a file, whatever its size. A single file pays it
  once; folder mode now pays it once a process.
- **Streamed video's resident size still reads 2x the file**: the input and output are
  mapped, and the system counts their pages while it can give them back at once. "Our
  peak" counts only what it cannot give back -- macOS's physical footprint.

## A video past 4 GiB (19 November)

The plan's target was a clip of several gigabytes cleaned within the memory target, with
the same output as the clean that holds the whole file. Past 4 GiB a QuickTime file's
chunk offsets become 64-bit (`co64`), where a streamed writer that patches offsets would
go wrong, so the clip is 4.5 GB: 75 minutes of 720p test pattern with sound.
`scripts/perf_big_video.py` builds it, cleans it both ways and compares.

| clean | time | our peak |
|---|--:|--:|
| streamed (the command's path) | 11-27 s over five runs | **220 MB** every run |
| whole file in memory (an archive member's path) | 7-10 s over two runs | 9.2 GB |

- **The two outputs are byte-identical** (sha256), keep `co64`, and carry the input's
  coded video and sound packet for packet (ffmpeg's `streamhash`, no decode).
- **Memory grows with the recording's length, not its size**: what the streamed clean
  holds is the clip's index of frames and sound packets, about 3 MB a minute at 30 frames
  a second with sound -- 44 MB for 17 minutes, 220 MB for 75. So the plan's "independent
  of the file's size" is not what was built; the memory check charges 0.1x the file,
  twice what this clip needed ([limit #38](limits.md)).
- Streaming is slower than holding the file once it is this large, and its time varies
  more (the output is written through mapped pages, so it waits on the disk: three runs
  in a row took 11 s, two others 20 and 27 s on a disk 84% full); it needs 40 times less
  memory, and that did not vary.

## Linux (19 November)

The same benchmark in the pinned image (`Dockerfile`: Ubuntu 24.04, Python 3.12.3,
ffmpeg 6.1.1, LibreOffice 24.2), on aarch64 Linux in Docker's VM on the same M1 Pro
(10 CPUs, 8 GB) -- so the difference from the table above is the system and the
programs, not the processor. The Mac has ffmpeg 8.1.1 and LibreOffice 26.2. Pictures
ran at 1 and 10 MB only: generating the 100 MB test photo needs more than the VM's
8 GB (the generator, not the clean).

```
./.venv/bin/python scripts/perf_bench.py --only jpeg png tiff --sizes 1 10 --big
./.venv/bin/python scripts/perf_bench.py --only mp3 flac m4a mp4 pdf docx xlsx pptx zip --sizes 1 10 100 --big 1000
```

| clean | file | macOS (8 Nov) | Linux (19 Nov) |
|---|--:|--:|--:|
| JPEG light | 9 MB | 0.03 s, 36 MB | 0.03 s, 18 MB |
| JPEG deep | 9 MB | 0.68 s, 407 MB | 0.59 s, 302 MB |
| JPEG full | 9 MB | 0.91 s, 597 MB | 0.85 s, 523 MB |
| PNG deep | 10 MB | 0.62 s, 154 MB | 0.73 s, 109 MB |
| MP3 full | 10 MB | 7.0 s, 129 MB | 10.1 s, 117 MB |
| M4A full | 8.7 MB | 4.7 s, 123 MB | 8.2 s, 88 MB |
| FLAC light | 97 MB | 0.08 s, 291 MB | 0.05 s, 194 MB |
| video light | 1 GB | 2.9 s, 44 MB | 2.4 s, 39 MB |
| PDF light | 94 MB | 11.7 s, 584 MB | 7.1 s, 460 MB |
| PDF full | 9.5 MB | 6.6 s, 66 MB | 5.1 s, 41 MB |
| Word light | 94 MB | 3.6 s, 487 MB | 3.6 s, 373 MB |
| Word full | 9.4 MB | 3.4 s, 66 MB | 0.9 s, 47 MB |
| Excel light | 9.7 MB | 11.6 s, 285 MB | 8.2 s, 196 MB |
| Excel light | 98 MB | not run | **82.7 s, 2.2 GB** |
| PowerPoint light | 94 MB | 4.1 s, 494 MB | 4.1 s, 378 MB |
| ZIP of photos | 903 MB | 43.3 s, 4.59 GB | 46.4 s, 4.53 GB |

- **Every Linux peak is at or below the Mac's**, so the memory check's factors, measured
  on the Mac, hold on Linux. (macOS's footprint also counts memory the allocator has
  freed but kept.)
- **The strongest audio cleans are 1.4-1.7x slower on Linux** and Word's strongest clean
  3.7x faster: those levels hand the file to ffmpeg and LibreOffice, whose versions
  differ (above). The light and deep cleans run our own code and stay within 1.7x of
  each other in either direction (the Mac ran Python 3.14, the image 3.12); PDF's light
  clean is the widest gap, 11.7 s against 7.1 s.
- **Excel's light clean is the slowest light clean in the tool**, about 1 MB/s on both
  systems: a 98 MB workbook (about 500 MB of XML) takes 83 s and 2.2 GB, 23x its file.
- Linux's peak is sampled every 2 ms, so the shortest cleans can finish between samples
  and read under their true peak (a 1 MB JPEG's light clean reads 0.7 MB, less than the
  file it holds); the rows above are long enough to be sampled.

## What is not measured

- **x86-64.** Every number here is an Apple M1 Pro's: macOS natively, Linux in Docker's
  arm64 VM.
- HEIC, camera RAW, programs, GIF, WebP, BMP, SVG, EMF/WMF and EPUB are not in the
  ladder.
- The deeper levels above 10 MB, and real files: these files are generated -- photos
  with edges and noise, pink noise, a test-pattern video.
