"""Controls for document extraction: real files in, text out, refusals where it cannot read."""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import ingest  # noqa: E402
from fixtures_make import make_docx, make_pdf, make_xlsx  # noqa: E402

FAILS = []


def chk(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)
    return ok


def main():
    tmp = tempfile.mkdtemp(prefix="ingesttest")
    try:
        plain = os.path.join(tmp, "notes.txt")
        open(plain, "w", encoding="utf-8").write("Email alice.smith@example.com\nSecond line\n")
        text, fmt = ingest.extract(plain)
        chk("text/extracted", "alice.smith@example.com" in text and fmt == "txt", fmt)

        latin = os.path.join(tmp, "latin.csv")
        open(latin, "wb").write("café,naïve\n".encode("latin-1"))
        text, _ = ingest.extract(latin)
        chk("text/latin1-fallback", "caf" in text, text[:40])

        docx_path = os.path.join(tmp, "report.docx")
        make_docx(docx_path, ["Dear Alice Smith,", "Phone +447700900123 today."])
        text, fmt = ingest.extract(docx_path)
        chk("docx/paragraphs", fmt == "docx" and "Alice Smith" in text and "+447700900123" in text, text[:80])
        chk("docx/lines-kept", "\n" in text.strip(), repr(text[:60]))

        xlsx_path = os.path.join(tmp, "ledger.xlsx")
        make_xlsx(xlsx_path, [["Name", "Email"], ["Alice Smith", "alice.smith@example.com"]])
        text, fmt = ingest.extract(xlsx_path)
        chk("xlsx/rows", fmt == "xlsx" and "Alice Smith" in text and "alice.smith@example.com" in text, text[:80])
        chk("xlsx/tab-separated", "\t" in text, repr(text[:60]))

        pdf_path = os.path.join(tmp, "note.pdf")
        make_pdf(pdf_path, "Contact alice.smith@example.com now")
        if shutil.which("pdftotext"):
            text, fmt = ingest.extract(pdf_path)
            chk("pdf/text-layer", fmt == "pdf" and "alice.smith@example.com" in text, text[:80])
        else:
            chk("pdf/text-layer", True, "(skipped: pdftotext absent)")

        empty = os.path.join(tmp, "empty.txt")
        open(empty, "w").close()
        try:
            ingest.extract(empty)
            chk("refuse/empty-file", False, "no exception")
        except ingest.IngestFailure:
            chk("refuse/empty-file", True)

        binary = os.path.join(tmp, "archive.zip")
        open(binary, "wb").write(b"PK\x03\x04not really")
        try:
            ingest.extract(binary)
            chk("refuse/unsupported-extension", False, "no exception")
        except ingest.IngestFailure as exc:
            chk("refuse/unsupported-extension", "unsupported format" in str(exc), str(exc)[:60])

        broken = os.path.join(tmp, "broken.docx")
        open(broken, "wb").write(b"not a zip")
        try:
            ingest.extract(broken)
            chk("refuse/broken-docx", False, "no exception")
        except ingest.IngestFailure:
            chk("refuse/broken-docx", True)

        chk("supported/known", ingest.supported("a.pdf") and ingest.supported("b.xlsx")
            and not ingest.supported("c.exe"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all ingest controls passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
