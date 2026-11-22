"""Controls for the local frontend server: routes, guards, and the demo pipeline."""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import ui_server  # noqa: E402

FAILS = []


def chk(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)
    return ok


def call(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    payload = None if body is None else json.dumps(body)
    conn.request(method, path, body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    headers = dict(resp.getheaders())
    conn.close()
    return resp.status, headers, raw


def main():
    tmp = tempfile.mkdtemp(prefix="uitest")
    httpd = ui_server.make_server(port=0, state_dir=tmp, names=["Alice Smith"])
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, raw = call(port, "GET", "/api/health")
        health = json.loads(raw)
        chk("health/ok", status == 200 and health["ok"] and health["live"] is False, raw[:120])
        chk("health/kinds", len(health["kinds"]) >= 10 and health.get("names") == 1, health.get("names"))
        chk("headers/no-store", "no-store" in headers.get("Cache-Control", ""), headers.get("Cache-Control"))

        status, _h, raw = call(port, "GET", "/")
        chk("ui/index-served", status == 200 and "desensitizer-core-vagrant" in raw, str(status))
        status, _h, raw = call(port, "GET", "/app.js")
        chk("ui/static-served", status == 200 and "btn-censor" in raw, str(status))
        status, _h, _raw = call(port, "GET", "/../../etc/passwd")
        chk("ui/traversal-blocked", status in (403, 404), str(status))
        status, _h, raw = call(port, "GET", "/api/nope")
        chk("api/unknown-404", status == 404 and "error" in raw, str(status))

        text = "mail alice.smith@example.com about STF-4821"
        status, _h, raw = call(port, "POST", "/api/censor", {"text": text})
        data = json.loads(raw)
        chk("api/censor", status == 200 and "alice.smith@example.com" not in data["censored"]
            and data["kinds"].get("email") == 1 and data["leaks"] == [], raw[:160])

        status, _h, raw = call(port, "POST", "/api/decensor", {"text": data["censored"]})
        chk("api/decensor", json.loads(raw).get("restored") == text, raw[:160])

        status, _h, raw = call(port, "POST", "/api/decensor", {"text": "email:AAAAAAAAAAAAAAAAAAAAAAAAAA"})
        chk("api/decensor-unknown-refused", "error" in json.loads(raw), raw[:160])

        status, _h, raw = call(port, "POST", "/api/run", {"text": text, "mode": "demo"})
        run = json.loads(raw)
        chk("api/run-demo", run.get("ok") is True and text in run["restored"], raw[:160])
        chk("api/run-demo-egress", "alice.smith@example.com" not in run["censored"], run["censored"][:80])

        status, _h, raw = call(port, "POST", "/api/run", {"text": text, "mode": "live"})
        chk("api/run-live-off-refused", json.loads(raw).get("refused", {}).get("stage") == "config", raw[:160])

        status, _h, _raw = call(port, "POST", "/api/censor", None)
        chk("api/bad-body-400", status == 400, str(status))

        status, _h, _raw = call(port, "POST", "/api/run", {"text": "   ", "mode": "demo"})
        chk("api/empty-text-400", status == 400, str(status))
    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all ui controls passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
