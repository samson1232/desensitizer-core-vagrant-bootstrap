"""Document text extraction — no model, no agent, no network.

Everything the aliaser can read, and how it reads it:

    .txt .md .markdown .csv .tsv .json .log      read as UTF-8 text
    .docx                                        zipfile + XML (stdlib)
    .xlsx .xlsm                                  zipfile + XML (stdlib), all sheets
    .pdf                                         pdftotext (poppler) or PyMuPDF, whichever is present

Office formats are ZIP containers of XML, so the standard library reads them without
python-docx or openpyxl. A PDF is the one format that needs an outside tool, and it
is reported as a refusal with the install command rather than a silent empty string —
an empty extraction is how a document gets "cleaned" without ever being scanned.
"""
import os
import re
import shutil
import subprocess
import zipfile
from xml.etree import ElementTree as ET

DOCX = ".docx"
XLSX = (".xlsx", ".xlsm")
PDF = ".pdf"
TEXTUAL = (".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".rst")

NS_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
NS_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

MAX_BYTES = 40 * 1024 * 1024


class IngestFailure(Exception):
    """The document could not be read. Never downgraded to an empty document."""


def supported(path):
    ext = os.path.splitext(path)[1].lower()
    return ext in TEXTUAL or ext == DOCX or ext in XLSX or ext == PDF


def extract(path):
    """Return (text, format) for one document. Raises IngestFailure."""
    if not os.path.isfile(path):
        raise IngestFailure(f"no such file: {path}")
    size = os.path.getsize(path)
    if size == 0:
        raise IngestFailure(f"{os.path.basename(path)} is empty")
    if size > MAX_BYTES:
        raise IngestFailure(f"{os.path.basename(path)} is {size} bytes, over the {MAX_BYTES} byte limit")

    ext = os.path.splitext(path)[1].lower()
    if ext in TEXTUAL:
        return _text_file(path), ext.lstrip(".")
    if ext == DOCX:
        return _docx(path), "docx"
    if ext in XLSX:
        return _xlsx(path), ext.lstrip(".")
    if ext == PDF:
        return _pdf(path), "pdf"
    raise IngestFailure(f"unsupported format {ext or '(none)'}; supported: "
                        + ", ".join(sorted(set(TEXTUAL) | {DOCX, *XLSX, PDF})))


def _text_file(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except UnicodeDecodeError:
        # A latin-1 fallback is a guess about bytes; say so rather than pretend.
        with open(path, "r", encoding="latin-1") as fh:
            return fh.read()


def _docx(path):
    try:
        with zipfile.ZipFile(path) as zf:
            with zf.open("word/document.xml") as fh:
                root = ET.parse(fh).getroot()
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise IngestFailure(f"{os.path.basename(path)} is not a readable .docx: {exc}") from None

    lines = []
    for para in root.iter(f"{NS_W}p"):
        parts = []
        for node in para.iter():
            if node.tag == f"{NS_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{NS_W}tab":
                parts.append("\t")
            elif node.tag == f"{NS_W}br":
                parts.append("\n")
        line = "".join(parts)
        if line.strip():
            lines.append(line)
    text = "\n".join(lines)
    if not text.strip():
        raise IngestFailure(f"{os.path.basename(path)} has no extractable text (scanned or empty?)")
    return text


def _col_index(ref):
    letters = re.match(r"([A-Z]+)", ref or "")
    if not letters:
        return 0
    total = 0
    for ch in letters.group(1):
        total = total * 26 + (ord(ch) - 64)
    return total


def _xlsx(path):
    try:
        with zipfile.ZipFile(path) as zf:
            shared = []
            if "xl/sharedStrings.xml" in zf.namelist():
                root = ET.parse(zf.open("xl/sharedStrings.xml")).getroot()
                for si in root.iter(f"{NS_S}si"):
                    shared.append("".join(t.text or "" for t in si.iter(f"{NS_S}t")))
            sheets = sorted(n for n in zf.namelist()
                            if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
            if not sheets:
                raise IngestFailure(f"{os.path.basename(path)} has no worksheets")
            out = []
            for sheet in sheets:
                root = ET.parse(zf.open(sheet)).getroot()
                out.append(f"[sheet: {os.path.basename(sheet)}]")
                for row in root.iter(f"{NS_S}row"):
                    cells = {}
                    for cell in row.iter(f"{NS_S}c"):
                        kind = cell.get("t")
                        value = ""
                        if kind == "s":
                            v = cell.find(f"{NS_S}v")
                            idx = int(v.text) if v is not None and v.text else -1
                            value = shared[idx] if 0 <= idx < len(shared) else ""
                        elif kind == "inlineStr":
                            value = "".join(t.text or "" for t in cell.iter(f"{NS_S}t"))
                        else:
                            v = cell.find(f"{NS_S}v")
                            value = v.text if v is not None and v.text else ""
                        if value:
                            cells[_col_index(cell.get("r"))] = value
                    if cells:
                        out.append("\t".join(cells.get(k, "") for k in sorted(cells)))
            text = "\n".join(out)
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        raise IngestFailure(f"{os.path.basename(path)} is not a readable workbook: {exc}") from None
    if not text.strip():
        raise IngestFailure(f"{os.path.basename(path)} has no extractable cells")
    return text


def _pdf(path):
    """pdftotext if present, else PyMuPDF. Neither present is a refusal, not an empty page."""
    exe = shutil.which("pdftotext")
    if exe:
        proc = subprocess.run([exe, "-layout", "-enc", "UTF-8", path, "-"],
                              capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise IngestFailure(f"pdftotext failed on {os.path.basename(path)}: {proc.stderr.strip()[:200]}")
        text = proc.stdout
        if not text.strip():
            raise IngestFailure(f"{os.path.basename(path)} has no text layer (scanned PDF?); "
                                "run OCR first — this package will not guess")
        return text

    try:
        import fitz  # type: ignore
    except ImportError:
        raise IngestFailure(
            "PDF support needs poppler (`apt install poppler-utils`) or PyMuPDF (`pip install pymupdf`); "
            "neither is present, and this package will not silently return an empty page") from None
    try:
        doc = fitz.open(path)
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
    except Exception as exc:  # fitz raises its own types
        raise IngestFailure(f"PyMuPDF failed on {os.path.basename(path)}: {exc}") from None
    if not text.strip():
        raise IngestFailure(f"{os.path.basename(path)} has no text layer (scanned PDF?)")
    return text
