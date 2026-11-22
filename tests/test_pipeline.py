"""End-to-end pipeline controls, with a fake transport standing in for the cloud.

The central assertion: whatever the transport received contained no value the
detectors can find. That is checked against the bytes the transport actually
received, not against what the pipeline says it sent.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import censor  # noqa: E402
import cloud  # noqa: E402
import pipeline  # noqa: E402
from fake_cloud import EchoTransport, FakeTransport  # noqa: E402

PROMPT = "Summarise the ticket: Alice Smith, alice.smith@example.com, STF-4821, site ops-ws-014."
FAILS = []


def chk(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)
    return ok


def fresh(tmp):
    key = os.path.join(tmp, "key.bin")
    vault = os.path.join(tmp, "vault.json")
    return censor.Censor(key, vault), key, vault


def main():
    tmp = tempfile.mkdtemp(prefix="pipetest")
    try:
        cens, key, vault = fresh(tmp)
        log = os.path.join(tmp, "runs.jsonl")
        transport = EchoTransport()
        client = cloud.CloudClient(base_url="https://api.example.com/v1", model="test-model",
                                   api_key="sk-test", transport=transport)

        result = pipeline.run(PROMPT, cens, client, log_path=log)
        chk("run/ok", result.ok and result.stage == "done", f"{result.stage} {result.reason}")
        chk("run/restored-equals-original", result.restored.startswith(PROMPT), result.restored[:80])
        chk("run/kinds-counted", result.kinds.get("email") == 1 and result.kinds.get("staff_id") == 1, result.kinds)

        sent = transport.last_prompt
        chk("egress/no-detectable-values", censor.detect(sent) == [], [k for _, _, k, _ in censor.detect(sent)])
        chk("egress/token-present", "email:" in sent and "alice.smith@example.com" not in sent)
        chk("egress/token-width", all(len(t) == censor.TOKEN_CHARS for t in censor.TOKEN_RE.findall(sent)[0][1:] or [""]),
            censor.TOKEN_RE.findall(sent)[:1])

        raw_log = open(log, encoding="utf-8").read()
        entry = json.loads(raw_log.strip().splitlines()[-1])
        chk("log/has-no-plaintext", "alice.smith@example.com" not in raw_log and "STF-4821" not in raw_log)
        chk("log/records-hashes", entry.get("status") == "ok" and len(entry.get("outbound_sha256", "")) == 64, entry.get("status"))
        chk("log/records-model", entry.get("model") == "test-model", entry.get("model"))

        chk("vault/written-after-run", os.path.exists(vault))
        chk("restore/no-tokens-left", censor.TOKEN_RE.search(result.restored) is None)
        chk("result/raw-preserved", result.raw == transport.last_prompt + transport.suffix)

        # A model that invents a token must be refused, not passed through.
        cens2, _, _ = fresh(os.path.join(tmp, "b") if os.makedirs(os.path.join(tmp, "b")) is None else tmp)
        invented = cloud.CloudClient(base_url="https://api.example.com/v1", model="m", api_key="k",
                                     transport=FakeTransport("here you go: email:AAAAAAAAAAAAAAAAAAAAAAAAAA"))
        try:
            pipeline.run("anything", cens2, invented)
            chk("gate/invented-token-refused", False, "no exception")
        except pipeline.RunRefused as exc:
            chk("gate/invented-token-refused", exc.stage == "restore", exc.stage)

        # A failing transport is a refusal, not a retry storm and not an empty answer.
        broken = cloud.CloudClient(base_url="https://api.example.com/v1", model="m", api_key="k",
                                   transport=FakeTransport(raw=b'{"error":"boom"}', status=500))
        try:
            pipeline.run("anything", cens, broken)
            chk("gate/cloud-error-refused", False, "no exception")
        except pipeline.RunRefused as exc:
            chk("gate/cloud-error-refused", exc.stage == "cloud", exc.stage)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all pipeline controls passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
