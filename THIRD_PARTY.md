# Third-party software

scrubmeta's own code is under the Apache License 2.0 (`LICENSE`). It depends on the
software below, which keeps its own licence.

## Python libraries installed with the tool

| library | licence | used for |
|---|---|---|
| [pikepdf](https://github.com/pikepdf/pikepdf) (bundles qpdf) | MPL-2.0 (qpdf: Apache-2.0) | reading and rewriting PDFs |
| [Pillow](https://python-pillow.org) | MIT-CMU | decoding and re-encoding pictures |
| [NumPy](https://numpy.org) | BSD-3-Clause and others | the audio check in the stronger cleans |

## Programs it runs, if they are installed

scrubmeta starts these as separate programs and does not link to them. None is needed
for the light clean of any format; `scrubmeta doctor` says which are present.

| program | licence | used for |
|---|---|---|
| [ExifTool](https://exiftool.org) | Artistic-1.0 or GPL-1.0-or-later | the independent check in the report |
| [FFmpeg](https://ffmpeg.org) | LGPL-2.1-or-later, or GPL depending on the build | audio deep and full cleans |
| [LAME](https://lame.sourceforge.io) | LGPL-2.0-or-later | MP3 full clean |
| [libjpeg-turbo](https://libjpeg-turbo.org) (`jpegtran`) | IJG, BSD-3-Clause, Zlib | JPEG lossless clean |
| [Poppler](https://poppler.freedesktop.org) (`pdftotext`, `pdftoppm`) | GPL-2.0-only or GPL-3.0-only | PDF deep and full cleans |
| [LibreOffice](https://www.libreoffice.org) | MPL-2.0 and others | Word full clean |

## The container image

The `Dockerfile` builds an image that **contains and distributes** the programs above,
along with Ubuntu 24.04 and the Liberation fonts (SIL OFL-1.1), each under its own
licence. Anyone publishing that image must meet those licences' terms, including
offering the source of the GPL components. The image's package list records the exact
versions (`dpkg -l` inside it); their sources are in the Ubuntu archive.
