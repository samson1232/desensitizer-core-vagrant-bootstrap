"""Controls for the alias -> value workflow on whole files."""
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import workspace  # noqa: E402
from fixtures_make import make_docx, make_xlsx  # noqa: E402

EMAIL = "alice.smith@example.com"
PHONE = "+447700900123"
FAILS = []


def chk(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)
    return ok


def no_values(text):
    return EMAIL not in text and PHONE not in text


def main():
    tmp = tempfile.mkdtemp(prefix="wstest")
    try:
        docx_path = os.path.join(tmp, "ticket.docx")
        make_docx(docx_path, [f"Contact {EMAIL}", f"Phone {PHONE}"])
        ws = workspace.Workspace(os.path.join(tmp, "root"), "acme", create=True, names=["Alice Smith"])

        chk("layout/private-mode", (os.stat(ws.private_dir).st_mode & 0o777) == 0o700)
        chk("layout/state-mode", (os.stat(ws.state_dir).st_mode & 0o777) == 0o700)
        chk("layout/work-exists", os.path.isdir(ws.work_dir))

        entry = ws.add(docx_path, keep_original=True)
        chk("add/id", entry["id"] == "doc-001", entry["id"])
        chk("add/kinds", entry["kinds"].get("email") == 1 and entry["kinds"].get("phone") == 1, entry["kinds"])

        work_file = os.path.join(ws.work_dir, "doc-001.md")
        body = open(work_file, encoding="utf-8").read()
        chk("alias/tokens-in-work", "email:" in body and "phone:" in body)
        chk("alias/no-values-in-work", no_values(body), body[:120])
        chk("alias/original-kept-private", os.path.isfile(os.path.join(ws.dir, entry["private_path"])))

        index = open(ws.index_path, encoding="utf-8").read()
        chk("index/no-values", no_values(index) and "doc-001" in index)

        manifest = open(ws.manifest_path, encoding="utf-8").read()
        chk("manifest/no-values", no_values(manifest))

        restored = ws.restore_file(work_file, fmt="md")
        restored_path = os.path.join(ws.dir, restored["restored"])
        text = open(restored_path, encoding="utf-8").read()
        chk("restore/values-back", EMAIL in text and PHONE in text, text[:80])
        chk("restore/no-tokens-left", "email:" not in text and "phone:" not in text)
        chk("restore/mode-0600", (os.stat(restored_path).st_mode & 0o777) == 0o600)
        chk("restore/dir-mode-0700", (os.stat(ws.restored_dir).st_mode & 0o777) == 0o700)
        chk("restore/name-from-doc", restored["restored"].endswith("doc-001--ticket.restored.md"), restored["restored"])

        docx_out = ws.restore_file(work_file, fmt="docx")
        with zipfile.ZipFile(os.path.join(ws.dir, docx_out["restored"])) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
        chk("restore/docx-format", EMAIL in xml, xml[:100])

        body_only = open(restored_path, encoding="utf-8").read()
        chk("restore/header-stripped",
            "desensitized:" not in body_only and not body_only.lstrip().startswith("# doc-001"),
            body_only[:60])

        # The agent's own output is restored the same way.
        agent_file = os.path.join(ws.work_dir, "doc-001-summary.md")
        open(agent_file, "w", encoding="utf-8").write("Summary for email:" + body.split("email:")[1].split()[0].rstrip("."))
        out = ws.restore_all(fmt="md")
        chk("restore-all/covers-agent-files", any(e["source"].endswith("doc-001-summary.md") for e in out),
            [e["source"] for e in out])

        bad = os.path.join(ws.work_dir, "doc-002.md")
        open(bad, "w", encoding="utf-8").write("contains email:AAAAAAAAAAAAAAAAAAAAAAAAAA")
        before = set(os.listdir(ws.restored_dir))
        try:
            ws.restore_file(bad, fmt="md")
            chk("restore/unknown-token-refused", False, "no exception")
        except Exception:
            chk("restore/unknown-token-refused", set(os.listdir(ws.restored_dir)) == before)

        log = open(os.path.join(ws.state_dir, "restores.jsonl"), encoding="utf-8").read()
        chk("restore-log/no-values", no_values(log) and "restored_sha256" in log)

        chk("status/documents", len(ws.status()["documents"]) == 1)
        ws2 = workspace.Workspace(os.path.join(tmp, "root"), "acme")
        chk("reopen/vault-usable", ws2.restore("email:" + body.split("email:")[1].split(",")[0].split()[0]) == EMAIL)

        try:
            workspace.Workspace(os.path.join(tmp, "root"), "Bad Name", create=True)
            chk("guard/project-name", False, "accepted a bad project name")
        except workspace.WorkspaceFailure:
            chk("guard/project-name", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all workspace controls passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
