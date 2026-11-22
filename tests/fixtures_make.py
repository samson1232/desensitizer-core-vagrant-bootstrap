"""Build real documents for the tests, with the standard library only.

A test that mocks the extractor proves nothing about the extractor, so these build
actual ZIP-based Office files and an actual PDF, and the suite reads them back.
"""
import zipfile


def make_pdf(path, text, wrap_at=78):
    """A minimal one-page PDF with Helvetica text, wrapped so it stays inside the page box.

    Wrapping matters: text that runs past the right margin is dropped by pdftotext on
    extraction, which would make this fixture lie about what a real document contains.
    """
    import textwrap

    lines = []
    for para in str(text).splitlines() or [""]:
        lines.extend(textwrap.wrap(para, width=wrap_at) or [""])
    shown = lines[:44]  # one page at 15pt leading
    ops = ["BT", "/F1 10 Tf", "12 TL", "72 750 Td"]
    for line in shown:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        ops.append(f"({escaped}) Tj T*")
    ops.append("ET")
    content = "\n".join(ops).encode("latin-1", "replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % index + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    with open(path, "wb") as fh:
        fh.write(bytes(out))


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(path, paragraphs):
    body = "".join(
        f"<w:p><w:r><w:t xml:space=\"preserve\">{p}</w:t></w:r></w:p>" for p in paragraphs
    )
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml",
                    '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Default Extension="xml" ContentType="application/xml"/>'
                    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
                    'officedocument.wordprocessingml.document.main+xml"/></Types>')
        zf.writestr("word/document.xml", document)


S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def make_xlsx(path, rows):
    """rows: list of list of strings. Written as shared strings, like Excel does."""
    shared, index = [], {}
    cells = []
    for r, row in enumerate(rows, 1):
        xml_cells = []
        for c, value in enumerate(row):
            if value not in index:
                index[value] = len(shared)
                shared.append(value)
            col = chr(ord("A") + c)
            xml_cells.append(f'<c r="{col}{r}" t="s"><v>{index[value]}</v></c>')
        cells.append(f'<row r="{r}">{"".join(xml_cells)}</row>')
    sheet = (f'<?xml version="1.0"?><worksheet xmlns="{S}"><sheetData>{"".join(cells)}</sheetData></worksheet>')
    sst = (f'<?xml version="1.0"?><sst xmlns="{S}" count="{len(shared)}" uniqueCount="{len(shared)}">'
           + "".join(f"<si><t>{v}</t></si>" for v in shared) + "</sst>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/sharedStrings.xml", sst)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
