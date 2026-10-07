"""ExePlugin — harness-side format knowledge for programs (ELF, Mach-O, PE).

Content identity is the program's machine code, read with independent parsers
(pyelftools, macholib, pefile -- never the scrubber's own walkers): every
executable section's bytes, hashed. Returns b"" when a parser is absent, so a
caller reports *not measured* rather than *unchanged*. Whether the program still
RUNS the same is the per-format tests' job (`test_exe_*.py`), which execute it.

`structural_features` is the A2 channel, and deliberately excludes the code. Two
compilers' code differs by definition and F1 keeps code by definition, so a code
hash in the categorical channel would fail every cell without saying anything --
the reason E-M4A keeps the coded-audio digest out of its channel. What it holds is
the container a toolchain lays around the code: section and segment names and
order, header versions, which notes and debug entries exist, and the file size.
"""
from __future__ import annotations

import hashlib
import io
import struct


def _kind(data: bytes) -> str | None:
    if data[:4] == b"\x7fELF":
        return "elf"
    if data[:2] == b"MZ":
        return "pe"
    if data[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
                    b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        return "macho"
    return None


class ExePlugin:
    format_id = "exe"

    def matches(self, header: bytes, path: str = "") -> bool:
        return _kind(header) is not None

    def annotate(self, in_path: str, offset: int) -> str | None:
        """The section or segment an offset falls in, for evidence labels."""
        data = open(in_path, "rb").read()
        try:
            for name, start, size in _regions(data):
                if start <= offset < start + size:
                    return name
        except Exception:                                 # noqa: BLE001
            return None
        return None

    def canonical_content(self, path: str) -> bytes:
        data = open(path, "rb").read()
        try:
            regions = _code_regions(data)
        except ImportError:
            return b""
        h = hashlib.sha256()
        for name, start, size in regions:
            h.update(name.encode() + data[start:start + size])
        return h.digest()

    def mandatory_constants(self) -> list[bytes]:
        """The marks F1 leaves, declared and generated from the code that writes
        them (limit #9's species: a program looks cleaned, never cleaned *from
        what*):

          * zeros wherever a value was blanked -- nothing may move, so a removed
            section or string keeps its length (limit #49);
          * the character a blanked Go stamp or module path is filled with, and
            the one shape every blanked commit time takes (`0000-00-00T00:00:00Z`);
          * the code-signature identifier every rewritten Mach-O carries, `a.out`,
            what ld64 and Go's linker write for an unnamed output.

        The first two are single fill characters: the guard strips them from the
        edges of a run's pieces, never splits at them, so a stamp that merely
        contains a zero is still caught. Runs joining a mark to the input's own
        bytes (a blanked value beside its field name) are explained by the guard,
        not declared. Bounded by a test asserting none carries a locus."""
        from src.scrub.formats.exe import codesign, go
        return [b"\0", bytes([go.BLANK]), go.BLANKED_TIME,
                codesign.CANONICAL_IDENTIFIER + b"\0"]

    def structural_features(self, path: str) -> dict:
        data = open(path, "rb").read()
        try:
            feats = {"elf": _elf_features, "macho": _macho_features,
                     "pe": _pe_features}[_kind(data)](data)
        except Exception:                                 # noqa: BLE001
            return {}
        feats["size"] = len(data)
        return feats


# --------------------------------------------------------------------------- #
# Independent readers
# --------------------------------------------------------------------------- #
def _regions(data: bytes) -> list[tuple[str, int, int]]:
    kind = _kind(data)
    if kind == "elf":
        from elftools.elf.elffile import ELFFile
        e = ELFFile(io.BytesIO(data))
        return [(s.name, s["sh_offset"], s["sh_size"]) for s in e.iter_sections()
                if s["sh_type"] != "SHT_NOBITS"]
    if kind == "pe":
        import pefile
        p = pefile.PE(data=data, fast_load=True)
        return [(s.Name.rstrip(b"\0").decode("latin-1"), s.PointerToRawData,
                 s.SizeOfRawData) for s in p.sections]
    return [(f"{seg}", off, size) for seg, off, size, _code in _macho_sections(data)]


def _code_regions(data: bytes) -> list[tuple[str, int, int]]:
    kind = _kind(data)
    if kind == "elf":
        from elftools.elf.elffile import ELFFile
        e = ELFFile(io.BytesIO(data))
        return [(s.name, s["sh_offset"], s["sh_size"]) for s in e.iter_sections()
                if s["sh_flags"] & 0x4 and s["sh_type"] != "SHT_NOBITS"]
    if kind == "pe":
        import pefile
        p = pefile.PE(data=data, fast_load=True)
        return [(s.Name.rstrip(b"\0").decode("latin-1"), s.PointerToRawData,
                 s.SizeOfRawData) for s in p.sections if s.Characteristics & 0x20]
    return [(name, off, size) for name, off, size, code in _macho_sections(data)
            if code]


def _macho_headers(data: bytes):
    import tempfile

    from macholib.MachO import MachO
    with tempfile.NamedTemporaryFile() as f:
        f.write(data)
        f.flush()
        return MachO(f.name).headers


def _macho_sections(data: bytes):
    from macholib.mach_o import segment_command, segment_command_64
    out = []
    for h in _macho_headers(data):
        for _lc, cmd, sects in h.commands:
            if isinstance(cmd, segment_command | segment_command_64):
                for s in sects or []:
                    seg = s.segname.rstrip(bytes(1)).decode()
                    name = seg + "," + s.sectname.rstrip(bytes(1)).decode()
                    code = bool(s.flags & 0x80000000)        # S_ATTR_PURE_INSTRUCTIONS
                    out.append((name, h.offset + s.offset, s.size, code))
    return out


def _elf_features(data: bytes) -> dict:
    from elftools.elf.elffile import ELFFile
    e = ELFFile(io.BytesIO(data))
    sections = list(e.iter_sections())
    notes = []
    for s in sections:
        if s["sh_type"] == "SHT_NOTE":
            notes += [(n["n_name"], n["n_type"]) for n in s.iter_notes()]
    return {
        "class_endian_machine": (e.elfclass, e.little_endian, e["e_machine"]),
        "type_flags": (e["e_type"], e["e_flags"]),
        "section_names": tuple(s.name for s in sections),
        "section_layout": tuple((s["sh_type"], s["sh_flags"], s["sh_addralign"])
                                for s in sections),
        "segments": tuple((p["p_type"], p["p_flags"], p["p_align"])
                          for p in e.iter_segments()),
        "notes": tuple(notes),
        "symbol_count": sum(s.num_symbols() for s in sections
                            if s["sh_type"] == "SHT_SYMTAB"),
    }


def _macho_features(data: bytes) -> dict:
    from macholib.mach_o import (
        build_version_command,
        dylib_command,
        segment_command,
        segment_command_64,
    )
    slices = []
    for h in _macho_headers(data):
        cmds, segs, build, dylibs = [], [], None, []
        for lc, cmd, sects in h.commands:
            cmds.append(lc.cmd)
            if isinstance(cmd, segment_command | segment_command_64):
                segs.append((cmd.segname.rstrip(b"\0"),
                             tuple(s.sectname.rstrip(b"\0") for s in sects or [])))
            elif isinstance(cmd, build_version_command):
                build = (cmd.platform, cmd.minos, cmd.sdk, cmd.ntools)
            elif isinstance(cmd, dylib_command):
                dylibs.append(cmd.current_version)
        slices.append((h.header.cputype, h.header.filetype, h.header.flags,
                       tuple(cmds), tuple(segs), build, tuple(dylibs)))
    return {"slices": tuple(s[:3] for s in slices),
            "load_commands": tuple(s[3] for s in slices),
            "segments": tuple(s[4] for s in slices),
            "build_version": tuple(s[5] for s in slices),
            "dylib_versions": tuple(s[6] for s in slices)}


def _pe_features(data: bytes) -> dict:
    import pefile
    p = pefile.PE(data=data)
    o = p.OPTIONAL_HEADER
    return {
        "machine_characteristics": (p.FILE_HEADER.Machine, p.FILE_HEADER.Characteristics),
        "linker_version": (o.MajorLinkerVersion, o.MinorLinkerVersion),
        "os_subsystem_versions": (o.MajorOperatingSystemVersion,
                                  o.MajorSubsystemVersion, o.MinorSubsystemVersion),
        "alignment": (o.SectionAlignment, o.FileAlignment),
        "dll_characteristics": o.DllCharacteristics,
        "section_names": tuple(s.Name.rstrip(b"\0") for s in p.sections),
        "section_flags": tuple(s.Characteristics for s in p.sections),
        "data_directories": tuple(i for i, d in enumerate(o.DATA_DIRECTORY) if d.Size),
        "debug_entries": tuple(d.struct.Type for d in
                               getattr(p, "DIRECTORY_ENTRY_DEBUG", [])),
        "rich_header": p.parse_rich_header() is not None,
        "coff_symbols": p.FILE_HEADER.NumberOfSymbols > 0,
        "checksum_set": o.CheckSum != 0,
        "dos_stub": hashlib.sha256(data[0x40:struct.unpack_from("<I", data, 0x3C)[0]])
        .hexdigest()[:16],
    }
