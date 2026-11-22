"""Controls for damaged text: the schema has to represent what extraction did to a value.

Three classes, and they are different questions:

* **loose** — a value with a space or a swapped separator inside it ("priya.nair @
  example.com", "00:1A:2B: 3C:4D:5E"). Still a complete value: tokenise it, restore it.
* **fragment** — the extractor clipped the tail ("00:1A:2B:3C:4D:5"). There is no value to
  tokenise, so the document is refused by default and marked needs_review if you insist.
* **intact** — nothing wrong; must not be flagged.
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import censor  # noqa: E402
import db as _db  # noqa: E402
import workspace as _workspace  # noqa: E402
from fixtures_make import make_docx  # noqa: E402

FAILS = []


def chk(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)
    return ok


def main():
    tmp = tempfile.mkdtemp(prefix="damaged")
    try:
        cens = censor.Censor(os.path.join(tmp, "k.bin"), os.path.join(tmp, "v.json"))

        damaged_email = "write to priya.nair @ example.com today"
        out, kinds = cens.censor_verified(damaged_email)
        chk("loose/email-with-space", kinds.get("email") == 1 and "priya.nair" not in out, out)
        chk("loose/email-method", cens.last_spans[0]["method"] == "loose", cens.last_spans)
        chk("loose/round-trip", cens.restore(out) == damaged_email)

        spaced_mac = "nic 00:1A:2B: 3C:4D:5E up"
        out2, kinds2 = cens.censor_verified(spaced_mac)
        chk("loose/mac-with-space", kinds2.get("mac") == 1 and "3C:4D:5E" not in out2, out2)
        chk("loose/mac-round-trip", cens.restore(out2) == spaced_mac)

        spaced_device = "host DESKTOP - 9941A rebooted"
        out3, kinds3 = cens.censor_verified(spaced_device)
        chk("loose/device-with-space", kinds3.get("device") == 1 and "9941A" not in out3, out3)

        clipped = "mac 00:1A:2B:3C:4D:5 end"
        frags = censor.fragments(clipped)
        chk("fragment/detected", len(frags) == 1 and frags[0]["kind"] == "mac", frags)
        try:
            cens.censor_verified(clipped)
            chk("fragment/refused-by-default", False, "no exception")
        except censor.CensorFailure as exc:
            chk("fragment/refused-by-default", "extraction damage" in str(exc), str(exc)[:80])

        out4, _ = cens.censor_verified(clipped, allow_fragments=True)
        chk("fragment/allowed-when-asked", "00:1A:2B:3C:4D:5" in out4)

        chk("intact/not-flagged", censor.fragments("the printer resumed normally") == [])
        chk("intact/timestamps-not-fragments", censor.fragments("at 08:14:51 on 2026-11-22") == [])

        # The whole record, in the database, for a document that needed review.
        docx_path = os.path.join(tmp, "damaged.docx")
        make_docx(docx_path, ["Device 00:1A:2B:3C:4D:5 was seen",
                              "Mail priya.nair @ example.com about it"])
        ws = _workspace.Workspace(os.path.join(tmp, "root"), "acme", create=True)
        entry = ws.add(docx_path, allow_fragments=True)
        chk("doc/status-needs-review", entry["status"] == "needs_review", entry["status"])
        chk("doc/fragment-recorded", len(entry["fragments"]) == 1, entry["fragments"])
        chk("doc/methods-recorded",
            all(s["method"] in ("strict", "loose", "fragment") for s in entry["spans"])
            and any(s["method"] == "loose" for s in entry["spans"]), entry["spans"])
        chk("doc/no-values-in-entry",
            all("priya.nair" not in str(v) for v in entry.values()), list(entry))

        body = open(os.path.join(ws.work_dir, "doc-001.md"), encoding="utf-8").read()
        chk("doc/review-banner", "REVIEW:" in body and "damaged by extraction" in body, body[:200])
        chk("doc/values-gone", "priya.nair" not in body and "3C:4D:5E" not in body)

        db_path = _db.init(os.path.join(tmp, "corpus.db"))
        _db.add_document(db_path, entry)
        spans = _db.spans(db_path, entry["id"])
        frags_db = _db.fragments(db_path, entry["id"])
        chk("db/spans-written", len(spans) == len(entry["spans"]), (len(spans), len(entry["spans"])))
        chk("db/method-column", any(s["method"] == "loose" for s in spans) or len(spans) >= 1, spans)
        chk("db/fragment-row", len(frags_db) == 1 and frags_db[0]["kind"] == "mac", frags_db)
        chk("db/status-column", _db.document(db_path, entry["id"])["status"] == "needs_review")
        blob = open(db_path, "rb").read()
        chk("db/no-values", b"priya.nair" not in blob and b"3C:4D:5E" not in blob)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all damaged-text controls passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
