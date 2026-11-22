"""Controls for the cloud client. No network: every call goes through a fake transport."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import cloud  # noqa: E402
from fake_cloud import FakeTransport  # noqa: E402

FAILS = []


def chk(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)
    return ok


def make(reply="hello", **kw):
    t = FakeTransport(reply=reply, **kw)
    return t, cloud.CloudClient(base_url="https://api.example.com/v1", model="test-model",
                                api_key="sk-test", transport=t)


def main():
    t, cl = make("hello there")
    chk("complete/returns-content", cl.complete("hi") == "hello there")
    body = t.last_body
    chk("request/model", body["model"] == "test-model", body.get("model"))
    chk("request/stream-false", body["stream"] is False, body.get("stream"))
    chk("request/user-message", body["messages"][-1] == {"role": "user", "content": "hi"})
    chk("request/auth-header", t.calls[0]["call"]["headers"]["Authorization"] == "Bearer sk-test")
    chk("request/url", t.calls[0]["call"]["url"] == "https://api.example.com/v1/chat/completions")
    chk("request/timeout-passed", t.calls[0]["timeout"] == cloud.DEFAULT_TIMEOUT)

    t2, cl2 = make("x")
    cl2.complete("hi", system="be terse")
    roles = [m["role"] for m in t2.last_body["messages"]]
    chk("request/system-included", roles == ["system", "user"], roles)

    t3, cl3 = make("x")
    cl3.complete("hi")
    chk("request/system-absent", [m["role"] for m in t3.last_body["messages"]] == ["user"])

    t4, cl4 = make(raw=b'{"error":"slow down"}', status=429, headers={"Retry-After": "7"})
    try:
        cl4.complete("hi")
        chk("error/429-raises", False, "no exception")
    except cloud.CloudError as exc:
        chk("error/429-raises", exc.status == 429 and exc.retry_after == "7", f"{exc.status} {exc.retry_after}")

    t5, cl5 = make(raw=json.dumps({"choices": []}).encode())
    try:
        cl5.complete("hi")
        chk("error/no-choices", False, "no exception")
    except cloud.CloudError:
        chk("error/no-choices", True)

    t6, cl6 = make(raw=b"<html>not json</html>")
    try:
        cl6.complete("hi")
        chk("error/non-json", False, "no exception")
    except cloud.CloudError:
        chk("error/non-json", True)

    t7, cl7 = make(reply="   ")
    try:
        cl7.complete("hi")
        chk("error/empty-content", False, "no exception")
    except cloud.CloudError:
        chk("error/empty-content", True)

    for name, kwargs in (("error/missing-key", {"api_key": None, "api_key_env": "VAGRANT_NOPE"}),
                         ("error/missing-model", {"model": None}),
                         ("error/missing-url", {"base_url": None})):
        base = {"base_url": "https://api.example.com/v1", "model": "m", "api_key": "k"}
        base.update(kwargs)
        try:
            cloud.CloudClient(**base)
            chk(name, False, "no exception")
        except cloud.CloudError:
            chk(name, True)

    os.environ["VAGRANT_TEST_KEY"] = "sk-from-env"
    cfg = {"cloud": {"base_url": "https://api.example.com/v1", "model": "m", "api_key_env": "VAGRANT_TEST_KEY"}}
    c = cloud.client_from_config(cfg, transport=FakeTransport("ok"))
    chk("config/key-from-env", c.api_key == "sk-from-env", c.api_key)

    # The real serializer, with urlopen stubbed: proves the bytes on the wire carry
    # the chat-completions body and not the wrapper the tests hand to a fake.
    import urllib.request
    captured = {}

    class _Resp:
        status = 200

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "wire ok"}}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        headers = {}

    def _fake_urlopen(req, timeout=None):
        captured["body"] = req.data
        captured["headers"] = dict(req.headers)
        return _Resp()

    real = urllib.request.urlopen
    urllib.request.urlopen = _fake_urlopen
    try:
        wire = cloud.CloudClient(base_url="https://api.example.com/v1", model="wire-model", api_key="sk-wire")
        out = wire.complete("wire test")
    finally:
        urllib.request.urlopen = real
    sent = json.loads(captured["body"].decode())
    chk("wire/body-is-chat-completions", out == "wire ok" and sent.get("model") == "wire-model"
        and isinstance(sent.get("messages"), list) and "json" not in sent, list(sent.keys()))
    chk("wire/auth-header", captured["headers"].get("Authorization") == "Bearer sk-wire", captured["headers"].get("Authorization"))

    print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all cloud controls passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
