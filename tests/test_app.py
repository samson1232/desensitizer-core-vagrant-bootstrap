"""Controls for the Flask + SQLite integration layer.

The important one is `db/no-plaintext`: the database is the record both layers trust, and it
must be impossible to read a real value out of it. That is asserted against the bytes on
disk (including the WAL), not against the schema.
"""
import base64
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

try:
    import app as app_module  # noqa: E402
except ImportError as exc:  # pragma: no cover - only when Flask is missing
    print(f"[FAIL] import/flask  {exc}")
    sys.exit(1)

from fixtures_make import make_docx  # noqa: E402

EMAIL = "alice.smith@example.com"
PHONE = "+447700900123"
FAILS = []


def chk(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)
    return ok


def fake_runner(agent, prompt, work_dir):
    return {"exit_code": 0, "output": f"ran in {work_dir}", "error": None}


def failing_runner(agent, prompt, work_dir):
    return {"exit_code": 3, "output": "", "error": "agent refused"}


def main():
    tmp = tempfile.mkdtemp(prefix="apptest")
    try:
        src = os.path.join(tmp, "ticket.docx")
        make_docx(src, [f"Contact {EMAIL}", f"Phone {PHONE}"])
        payload = base64.b64encode(open(src, "rb").read()).decode()

        app = app_module.create_app(os.path.join(tmp, "corpus"), "acme", names=["Alice Smith"],
                                    runner=fake_runner)
        client = app.test_client()
        db_path = app.config["CORPUS"]["db"]

        health = client.get("/api/health").get_json()
        chk("health/ok", health["ok"] and health["project"] == "acme", health)
        chk("health/db-path", db_path.endswith("corpus.db") and os.path.exists(db_path), db_path)
        chk("health/kinds", "email" in health["kinds"] and "name" in health["kinds"], health["kinds"])

        r = client.post("/api/upload", json={"name": "ticket.docx", "data": payload})
        body = r.get_json()
        chk("upload/accepted", r.status_code == 200 and body["document"]["id"] == "doc-001",
            r.status_code)
        chk("upload/row-returned", [d["id"] for d in body["workspace"]["documents"]] == ["doc-001"])

        work_file = os.path.join(tmp, "corpus", "acme", "work", "doc-001.md")
        text = open(work_file, encoding="utf-8").read()
        chk("work/tokenised", "email:" in text and EMAIL not in text, text[:120])

        # the integration record must not carry values, WAL included
        blob = b""
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(db_path + suffix):
                blob += open(db_path + suffix, "rb").read()
        chk("db/no-plaintext", EMAIL.encode() not in blob and PHONE.encode() not in blob)

        docs = client.get("/api/documents").get_json()["documents"]
        chk("documents/listed", len(docs) == 1 and docs[0]["kinds"]["email"] == 1, docs)

        r = client.post("/api/documents/doc-001/restore", json={"fmt": "md"})
        restored = r.get_json().get("restore", {})
        chk("restore/accepted", r.status_code == 200 and "restored" in restored, r.get_json())
        restored_file = os.path.join(tmp, "corpus", "acme", restored.get("restored", "missing"))
        restored_text = open(restored_file, encoding="utf-8").read()
        chk("restore/values-back", EMAIL in restored_text and PHONE in restored_text)

        r = client.get(restored.get("download", "/api/download/nope"))
        chk("download/serves-restored", r.status_code == 200 and EMAIL.encode() in r.data, r.status_code)

        chk("restore/unknown-doc-404", client.post("/api/documents/doc-999/restore", json={}).status_code == 404)

        r = client.post("/api/jobs", json={"prompt": "Summarise doc-001", "doc_ids": ["doc-001"]})
        job = r.get_json()["job"]
        chk("job/created", r.status_code == 201 and job["status"] == "queued", job)
        chk("job/command-owner", job["agent"] == "desens-acme", job["agent"])

        client.post(f"/api/jobs/{job['id']}/run")
        state = {}
        for _ in range(50):
            state = client.get(f"/api/jobs/{job['id']}").get_json()["job"]
            if state["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        chk("job/ran", state.get("status") == "done" and state.get("exit_code") == 0, state.get("status"))
        chk("job/output-recorded", "acme/work" in (state.get("output") or ""), state.get("output"))

        app2 = app_module.create_app(os.path.join(tmp, "corpus2"), "beta", runner=failing_runner)
        c2 = app2.test_client()
        j2 = c2.post("/api/jobs", json={"prompt": "do the thing"}).get_json()["job"]
        c2.post(f"/api/jobs/{j2['id']}/run")
        st = {}
        for _ in range(50):
            st = c2.get(f"/api/jobs/{j2['id']}").get_json()["job"]
            if st["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        chk("job/failure-recorded", st.get("status") == "failed" and st.get("error") == "agent refused", st)

        r = client.post("/api/upload", json={"name": "x.exe", "data": payload})
        chk("upload/unsupported-400", r.status_code == 400, r.status_code)

        idx = client.get("/api/index").get_json()["index"]
        chk("index/no-values", EMAIL not in idx and "doc-001" in idx)

        r = client.post("/api/run", json={"text": f"mail {EMAIL}", "mode": "demo"})
        chk("run/demo-ok", r.status_code == 200 and r.get_json().get("ok") is True, r.status_code)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all integration-layer controls passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
