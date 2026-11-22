#!/usr/bin/env python3
"""Tiny local server for the vagrant frontend. Standard library only.

    python3 ui_server.py                        # http://127.0.0.1:8799  (demo mode only)
    python3 ui_server.py --live --env-file ~/.hermes/.env \
        --api-key-env DEEPSEEK_API_KEY --base-url https://api.deepseek.com/v1 --model deepseek-chat

Binds loopback only. The API key never leaves this process: the browser talks to
/api/run, the server censors, calls the cloud, restores, and returns text. Demo
mode answers locally with no network at all, so the flow is testable before you
have a key.

Endpoints
    GET  /api/health                 service, model, whether live is configured
    POST /api/censor   {text}        censored text + tokens (no network)
    POST /api/decensor {text}        restore tokens (no network)
    POST /api/run      {text,mode}   full pipeline: censor -> cloud -> restore
"""
import argparse
import http.server
import json
import os
import re
import socketserver
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import censor  # noqa: E402
import cloud  # noqa: E402
import pipeline  # noqa: E402

STATE_DIR = os.path.join(HERE, ".vagrant_state")
UI_DIR = os.path.join(HERE, "ui")
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


class DemoTransport:
    """Offline stand-in for the cloud: deterministic, no sockets, no key."""

    PREFIX = ("Here is the rewrite you asked for. I kept every identifier exactly as given, "
              "since changing one would break the mapping:\n\n")
    SUFFIX = ("\n\nNothing above was interpreted: the placeholders were treated as opaque ids.")

    def __init__(self):
        self.model = "demo-echo"
        self.calls = 0

    def __call__(self, call, timeout=None):
        self.calls += 1
        prompt = call["json"]["messages"][-1]["content"]
        body = json.dumps({"choices": [{"message": {"role": "assistant",
                                                   "content": self.PREFIX + prompt + self.SUFFIX}}]}).encode()
        return 200, {}, body


def load_env_file(path):
    """Read KEY=VALUE lines for keys not already in the environment. Returns count."""
    if not path or not os.path.exists(path):
        return 0
    added = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
                added += 1
    return added


class App:
    def __init__(self, state_dir=STATE_DIR, transport=None, client=None, model_label=None, names=None):
        self.state_dir = state_dir
        os.makedirs(state_dir, exist_ok=True)
        self.censors = censor.Censor(os.path.join(state_dir, "key.bin"),
                                     os.path.join(state_dir, "vault.json"), names=names)
        self.demo_transport = transport or DemoTransport()
        self.live_client = client
        self.log_path = os.path.join(state_dir, "runs.jsonl")
        self.model_label = model_label or ("live" if client else "demo-echo")

    # -- operations -------------------------------------------------------
    def do_censor(self, text):
        censored, kinds = self.censors.censor(text)
        self.censors.save()
        tokens = [{"kind": k, "token": f"{k}:{t}"} for k, t in censor.TOKEN_RE.findall(censored)]
        return {"censored": censored, "kinds": kinds, "tokens": tokens,
                "leaks": [k for _, _, k, _ in censor.detect(censored)]}

    def do_decensor(self, text):
        try:
            return {"restored": self.censors.restore(text)}
        except censor.CensorFailure as exc:
            return {"error": str(exc)}

    def do_run(self, text, mode):
        client = self.live_client if mode == "live" else _ClientWrapper(self.demo_transport)
        if mode == "live" and client is None:
            return {"refused": {"stage": "config", "reason": "live mode is off; start the server with --live and an API key"}}
        try:
            result = pipeline.run(text, self.censors, client, log_path=self.log_path)
        except pipeline.RunRefused as exc:
            return {"refused": {"stage": exc.stage, "reason": exc.reason}}
        return {"ok": True, "censored": result.censored, "raw": result.raw, "restored": result.restored,
                "kinds": result.kinds, "elapsed_ms": result.elapsed_ms, "run_id": result.run_id,
                "mode": mode, "model": result.model}


class _ClientWrapper:
    """Adapts the demo transport to the CloudClient interface used by pipeline.run."""

    def __init__(self, transport):
        self.transport = transport
        self.model = getattr(transport, "model", "demo-echo")

    def complete(self, prompt, system=None, max_tokens=None):
        call = {"url": "demo://local", "headers": {}, "json": {"model": self.model,
                "messages": [{"role": "user", "content": prompt}]}}
        _status, _headers, raw = self.transport(call, 0)
        return json.loads(raw)["choices"][0]["message"]["content"]


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "vagrant-ui"
    app = None  # set by make_server

    def log_message(self, fmt, *args):
        sys.stderr.write("[ui] %s\n" % (fmt % args))

    # -- helpers ----------------------------------------------------------
    def _send(self, status, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload))

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 2_000_000:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return None

    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        rel = rel[len("ui/"):] if rel.startswith("ui/") else rel
        full = os.path.realpath(os.path.join(UI_DIR, rel))
        if not full.startswith(os.path.realpath(UI_DIR) + os.sep):
            return self._send(403, "forbidden", "text/plain; charset=utf-8")
        if not os.path.isfile(full):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        ext = os.path.splitext(full)[1]
        with open(full, "rb") as fh:
            self._send(200, fh.read(), STATIC_TYPES.get(ext, "application/octet-stream"))

    # -- routes -----------------------------------------------------------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/health":
            app = self.app
            kinds = [k for k, _ in censor.PATTERNS] + (["name"] if app.censors.names else [])
            return self._json(200, {"ok": True, "service": "desensitizer-core-vagrant",
                                    "live": app.live_client is not None, "model": app.model_label,
                                    "kinds": kinds, "names": len(app.censors.names),
                                    "version": "0.1.0"})
        if path.startswith("/api/"):
            return self._json(404, {"error": "unknown endpoint"})
        return self._static(path)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        payload = self._read_json()
        if payload is None:
            return self._json(400, {"error": "expected a JSON body"})
        text = payload.get("text") or ""
        app = self.app
        if path == "/api/censor":
            return self._json(200, app.do_censor(text))
        if path == "/api/decensor":
            return self._json(200, app.do_decensor(text))
        if path == "/api/run":
            mode = payload.get("mode", "demo")
            if mode not in ("demo", "live"):
                return self._json(400, {"error": "mode must be demo or live"})
            if not text.strip():
                return self._json(400, {"error": "nothing to send"})
            return self._json(200, app.do_run(text, mode))
        return self._json(404, {"error": "unknown endpoint"})


class _Server(socketserver.ThreadingTCPServer):
    # Without this a restart inside the TIME_WAIT window fails with EADDRINUSE,
    # which is exactly what "restart the UI to pick up a fix" looks like.
    allow_reuse_address = True
    daemon_threads = True


def make_server(port=8799, host="127.0.0.1", state_dir=STATE_DIR, transport=None, client=None,
                model_label=None, names=None):
    app = App(state_dir=state_dir, transport=transport, client=client, model_label=model_label,
              names=names)
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = _Server((host, port), handler)
    return httpd


def build_live_client(args):
    if not args.live:
        return None
    key = os.environ.get(args.api_key_env or "", "") or None
    if not key:
        raise SystemExit(f"ui_server: --live needs an API key in ${args.api_key_env}")
    return cloud.CloudClient(base_url=args.base_url, model=args.model, api_key=key,
                             timeout=args.timeout, max_tokens=args.max_tokens)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Local UI for desensitizer-core-vagrant")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--state-dir", default=STATE_DIR)
    ap.add_argument("--live", action="store_true", help="allow mode=live calls to a cloud model")
    ap.add_argument("--env-file", help="optional KEY=VALUE file to load before reading the key")
    ap.add_argument("--api-key-env", default="VAGRANT_API_KEY")
    ap.add_argument("--base-url", default=os.environ.get("VAGRANT_BASE_URL"))
    ap.add_argument("--model", default=os.environ.get("VAGRANT_MODEL"))
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--names", default=os.environ.get("VAGRANT_NAMES", ""),
                    help="comma-separated name roster to censor as kind 'name'")
    ap.add_argument("--names-file", default=None, help="file with one name per line")
    args = ap.parse_args(argv)

    if args.env_file:
        load_env_file(args.env_file)
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    if args.names_file:
        try:
            with open(args.names_file, encoding="utf-8") as fh:
                names += [line.strip() for line in fh if line.strip() and not line.startswith("#")]
        except OSError as exc:
            raise SystemExit(f"ui_server: cannot read names file: {exc}")
    client = build_live_client(args) if args.live else None
    if args.live and not (args.base_url and args.model):
        raise SystemExit("ui_server: --live needs --base-url and --model")
    label = args.model if client else "demo-echo"

    httpd, last_error = None, None
    for candidate in [args.port] + [args.port + i for i in range(1, 10)]:
        try:
            httpd = make_server(port=candidate, host=args.host, state_dir=args.state_dir,
                                client=client, model_label=label, names=names)
            args.port = candidate
            break
        except OSError as exc:  # port busy or held in TIME_WAIT: try the next one
            last_error = exc
    if httpd is None:
        raise SystemExit(f"ui_server: no free port in {args.port}-{args.port + 9}: {last_error}")
    print(f"desensitizer-core-vagrant UI  ->  http://{args.host}:{args.port}/")
    print(f"  mode: {'live + demo' if client else 'demo only (no key, no network)'}   model: {label}"
          f"   names: {len(names)}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
