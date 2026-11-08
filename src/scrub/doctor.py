"""`doctor`: what this install can do, and what it would need to do more.

The light clean of every format is pure Python. The deeper levels of a few hand the
file to another program, and the report's independent check is ExifTool, so what an
install can do depends on what else is on the machine. A level whose program is
missing refuses at run time (exit 9); this says so before anyone tries, with the
versions, which also identify the setup a cleaned file came from (`docs/
november_plan.md`, E-INSTALL).

Exit 0 when everything is present, 9 when a program is missing -- the code a clean
would exit with for the same reason.
"""
from __future__ import annotations

import importlib
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass

from . import __version__
from .dispatch import default_dispatcher


@dataclass(frozen=True)
class Program:
    purpose: str
    version_args: tuple[str, ...]
    install: str                # what to install, by package manager


PROGRAMS = {
    "exiftool": Program("the independent check printed after each clean", ("-ver",),
                        "brew install exiftool / apt install libimage-exiftool-perl"),
    "jpegtran": Program("JPEG lossless clean", ("-version",),
                        "brew install jpeg-turbo / apt install libjpeg-turbo-progs"),
    "ffmpeg": Program("audio deep and full cleans", ("-version",),
                      "brew install ffmpeg / apt install ffmpeg"),
    "lame": Program("MP3 full clean", ("--version",),
                    "brew install lame / apt install lame"),
    "pdftotext": Program("PDF deep clean's word check", ("-v",),
                         "brew install poppler / apt install poppler-utils"),
    "pdftoppm": Program("PDF full clean", ("-v",),
                        "brew install poppler / apt install poppler-utils"),
    "soffice": Program("Word full clean", ("--version",),
                       "install LibreOffice / apt install libreoffice-writer"),
}

# Which programs each level hands the file to. A level not listed is pure Python.
NEEDS = {
    ("jpeg", "F2"): ("jpegtran",),
    ("mp3", "F3"): ("ffmpeg", "lame"),
    ("m4a", "F2"): ("ffmpeg",),
    ("m4a", "F3"): ("ffmpeg",),
    ("flac", "F2"): ("ffmpeg",),
    ("pdf", "F2"): ("pdftotext",),
    ("pdf", "F3"): ("pdftoppm",),
    ("docx", "F3"): ("soffice",),
}

LIBRARIES = {"pikepdf": "pikepdf", "Pillow": "PIL", "NumPy": "numpy"}

_VERSION = re.compile(r"\d+(?:\.\d+)+[\w.-]*")


def program_version(name: str, path: str) -> str:
    """The first version-shaped word the program prints, or "version unknown". Some
    of these print to stderr (jpegtran, poppler), so both are read."""
    try:
        proc = subprocess.run([path, *PROGRAMS[name].version_args], capture_output=True,
                              text=True, errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return "version unknown"
    m = _VERSION.search(proc.stdout + "\n" + proc.stderr)
    return m.group(0) if m else "version unknown"


def survey() -> tuple[dict[str, str | None], dict[str, str | None]]:
    """(program -> version or None if missing, library -> version or None)."""
    programs: dict[str, str | None] = {}
    for name in PROGRAMS:
        path = shutil.which(name)
        programs[name] = program_version(name, path) if path else None
    libraries: dict[str, str | None] = {}
    for label, module in LIBRARIES.items():
        try:
            libraries[label] = getattr(importlib.import_module(module), "__version__", "?")
        except ImportError:
            libraries[label] = None
    return programs, libraries


def missing_for(fmt: str, level: str, programs: dict[str, str | None]) -> list[str]:
    return [p for p in NEEDS.get((fmt, level), ()) if programs.get(p) is None]


def report() -> tuple[str, int]:
    """The text to print and the exit code."""
    programs, libraries = survey()
    lines = [f"scrub {__version__}  (Python {platform.python_version()}, "
             f"{platform.system()} {platform.machine()})", "", "Programs"]
    for name, program in PROGRAMS.items():
        version = programs[name]
        lines.append(f"  {name:<10} {version or 'MISSING':<16} {program.purpose}"
                     + ("" if version else f"\n             to install: {program.install}"))
    lines += ["", "Python libraries"]
    lines += [f"  {label:<10} {version or 'MISSING'}" for label, version in libraries.items()]
    lines += ["", "Cleaning levels"]
    for handler in default_dispatcher()._handlers:
        cells = []
        for level in handler.fidelities:
            gone = missing_for(handler.format_id, level, programs)
            cells.append(f"{level} yes" if not gone else f"{level} NO (needs {', '.join(gone)})")
        lines.append(f"  {handler.format_id:<10} " + ", ".join(cells))
    if programs["exiftool"] is None:
        lines += ["", "Without ExifTool the report cannot include its independent check."]
    complete = all(programs.values()) and all(libraries.values())
    return "\n".join(lines), 0 if complete else 9
