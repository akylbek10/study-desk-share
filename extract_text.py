#!/usr/bin/env python3
"""Turn downloaded PDFs, slides and Word docs into plain text in ./text,
mirroring the ./courses folder layout. Run: .venv/bin/python extract_text.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC, DST = ROOT / "courses", ROOT / "text"


def pdf(p):
    from pypdf import PdfReader
    r = PdfReader(str(p))
    return "\n\n".join(f"--- page {i} ---\n{(pg.extract_text() or '').strip()}" for i, pg in enumerate(r.pages, 1))


def pptx(p):
    from pptx import Presentation
    out = []
    for i, slide in enumerate(Presentation(str(p)).slides, 1):
        parts = [sh.text_frame.text for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
        for sh in slide.shapes:
            if sh.has_table:
                parts += [" | ".join(c.text for c in row.cells) for row in sh.table.rows]
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
            parts.append("Notes: " + slide.notes_slide.notes_text_frame.text)
        out.append(f"--- slide {i} ---\n" + "\n".join(parts))
    return "\n\n".join(out)


def docx(p):
    import docx as d
    doc = d.Document(str(p))
    parts = [para.text for para in doc.paragraphs if para.text.strip()]
    for t in doc.tables:
        parts += [" | ".join(c.text for c in row.cells) for row in t.rows]
    return "\n".join(parts)


def _xml_text(p, pattern):
    """Read text straight from the Office XML, skipping damaged media files."""
    import re
    import zipfile
    z = zipfile.ZipFile(p)
    names = sorted((n for n in z.namelist() if re.fullmatch(pattern, n)),
                   key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)) if re.search(r"\d", n) else 0)
    out = []
    for i, n in enumerate(names, 1):
        xml = z.read(n).decode("utf8", "ignore")
        runs = r"<a:t>([^<]*)</a:t>|<w:t[^>]*>([^<]*)</w:t>"
        paras = ["".join(a or b for a, b in re.findall(runs, para))
                 for para in re.split(r"</a:p>|</w:p>", xml)]
        out.append(f"--- part {i} ---\n" + "\n".join(x for x in paras if x.strip()))
    return "\n\n".join(out)


def pdf_fallback(p):
    import shutil
    import subprocess
    if shutil.which("pdftotext"):
        return subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True, timeout=300).stdout
    from pypdf import PdfReader
    out = []
    for i, pg in enumerate(PdfReader(str(p)).pages, 1):
        try:
            out.append(f"--- page {i} ---\n{pg.extract_text() or ''}")
        except Exception:
            out.append(f"--- page {i} --- (unreadable)")
    return "\n\n".join(out)


HANDLERS = {".pdf": pdf, ".pptx": pptx, ".docx": docx}
FALLBACKS = {
    ".pdf": pdf_fallback,
    ".pptx": lambda p: _xml_text(p, r"ppt/slides/slide\d+\.xml"),
    ".docx": lambda p: _xml_text(p, r"word/document\.xml"),
}


def main():
    ok = empty = failed = 0
    for f in sorted(SRC.rglob("*")):
        h = HANDLERS.get(f.suffix.lower())
        if not h:
            continue
        out = DST / f.relative_to(SRC).with_suffix(f.suffix + ".txt")
        if out.exists() and out.stat().st_mtime >= f.stat().st_mtime:
            continue
        try:
            try:
                text = h(f).strip()
            except Exception:
                text = FALLBACKS[f.suffix.lower()](f).strip()
        except Exception as e:
            print(f"! {f.relative_to(SRC)}: {e}")
            failed += 1
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        if len(text) < 50:
            empty += 1
        ok += 1
    print(f"Extracted {ok} files ({empty} nearly empty, likely scanned images), {failed} failed. Output: {DST}")


if __name__ == "__main__":
    main()
