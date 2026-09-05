"""OOXML package layer, shared by DOCX / XLSX / PPTX.

DOCX, XLSX and PPTX are the same container with three different main parts. The ZIP
reader and writer, the OPC relationship graph, `[Content_Types].xml` and
`docProps/*` are identical across all three, so they live here and the per-format
handlers consume them — the CLAUDE.md rule that a shared module is written once,
because a missed copy is a leak.

Nothing here is speculative generalisation: `docProps/app.xml` leaks
`TitlesOfParts` in a DOCX and **sheet names** in an XLSX through the same element.
"""
