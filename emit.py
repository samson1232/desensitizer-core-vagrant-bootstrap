"""Write restored text back out as a real file.

Aliasing flattens a document to text. Restoring gets the values back, and this module
puts that text back into a file you can open — `.md`/`.txt` verbatim, `.docx` as one
paragraph per line, `.xlsx` as tab-separated rows.

What it does not do is preserve the original layout: styles, images, columns and cell
formats are gone the moment a document is flattened, and pretending otherwise would be a
worse lie than saying it plainly. PDF output is refused for the same reason — there is no
writer here, and a fake PDF is not a document.
"""
import os
import zipfile

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

DOCX_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)

DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)

SUPPORTED = ("md", "markdown", "txt", "docx", "xlsx", "csv")


class EmitFailure(Exception):
    """The restored text cannot be written in the format asked for."""


def _escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def emit(text, path, fmt=None):
    """Write ``text`` to ``path`` in ``fmt`` (guessed from the extension when omitted)."""
    fmt = (fmt or os.path.splitext(path)[1].lstrip(".") or "md").lower()
    if fmt not in SUPPORTED:
        raise EmitFailure(f"cannot write .{fmt}; supported: {', '.join(SUPPORTED)} "
                          "(PDF is refused on purpose — flattening loses the layout)")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    if fmt in ("md", "markdown", "txt"):
        _write_text(text, path)
    elif fmt == "csv":
        _write_text(text, path)
    elif fmt == "docx":
        _write_docx(text, path)
    elif fmt == "xlsx":
        _write_xlsx(text, path)
    os.chmod(path, 0o600)
    return path


def _write_text(text, path):
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _write_docx(text, path):
    paragraphs = []
    for line in text.splitlines():
        run = f'<w:r><w:t xml:space="preserve">{_escape(line)}</w:t></w:r>' if line else ""
        paragraphs.append(f"<w:p>{run}</w:p>")
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document xmlns:w="{W}"><w:body>{"".join(paragraphs)}</w:body></w:document>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", DOCX_TYPES)
        zf.writestr("_rels/.rels", DOCX_RELS)
        zf.writestr("word/document.xml", document)


def _write_xlsx(text, path):
    rows = []
    for r, line in enumerate(text.splitlines(), 1):
        cells = []
        for c, value in enumerate(line.split("\t")):
            col = chr(ord("A") + c) if c < 26 else f"A{chr(ord('A') + c - 26)}"
            cells.append(f'<c r="{col}{r}" t="inlineStr"><is><t xml:space="preserve">{_escape(value)}</t></is></c>')
        rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    sheet = (f'<?xml version="1.0"?><worksheet xmlns="{S}"><sheetData>{"".join(rows)}</sheetData></worksheet>')
    string_table = (f'<?xml version="1.0"?><sst xmlns="{S}" count="0" uniqueCount="0"/>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/sharedStrings.xml", string_table)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
