#!/usr/bin/env python3
"""Flask integration layer: aliasing on one side, the Hermes agent on the other.

    uv venv && uv pip install -r requirements.txt
    .venv/bin/python app.py --project acme [--live --base-url ... --model ...]

The two layers never call each other directly. Everything crosses through the SQLite file in
`<project>/state/corpus.db`: a document row when something is aliased, a job row when the
agent is asked to work, a restore row when aliased text comes back as values. That keeps the
sequence auditable and means the agent's failure mode is a row with an error in it, not a
lost document.

Route map (the first block is the same contract the stdlib `ui_server.py` serves, so one
frontend drives both):

    GET  /api/health              service, model, workspace
    POST /api/censor  {text}      censor only, no network
    POST /api/decensor{text}      restore a snippet
    POST /api/run     {text,mode} one prompt through the cloud pipeline
    POST /api/upload  {name,data} alias a document into work/, insert the row
    GET  /api/workspace           project paths + documents
    GET  /api/index               work/INDEX.md

    GET  /api/documents           rows from the database
    POST /api/documents/<id>/restore {fmt}   alias -> value for one document
    GET  /api/download/<name>     download a restored file (restored/ only)
    POST /api/jobs    {prompt,doc_ids,agent}
    GET  /api/jobs, /api/jobs/<id>
    POST /api/jobs/<id>/run       hand the job to `hermes -p <agent> chat -q <prompt> --oneshot`
"""
import argparse
import base64
import os
import shutil
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, abort, jsonify, request, send_from_directory  # noqa: E402

import cloud  # noqa: E402
import db as _db  # noqa: E402
import emit as _emit  # noqa: E402
import ingest as _ingest  # noqa: E402
import pipeline  # noqa: E402
import workspace as _workspace  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(HERE, "ui")
VERSION = "0.2.0"
LOCK = threading.Lock()


def hermes_runner(agent, prompt, work_dir, timeout=900):
    """Default job runner: one non-interactive Hermes turn, cwd confined to work/."""
    exe = shutil.which("hermes") or "hermes"
    proc = subprocess.run([exe, "-p", agent, "chat", "-q", prompt, "--oneshot"],
                          cwd=work_dir, capture_output=True, text=True, timeout=timeout)
    return {"exit_code": proc.returncode, "output": (proc.stdout or "")[-20000:],
            "error": (proc.stderr or "")[-4000:] or None}


def create_app(root, project, names=None, client=None, runner=None, state_dir=None,
               model_label=None):
    ws = _workspace.Workspace(root, project, names=names, create=True)
    db_path = _db.init(os.path.join(state_dir or ws.state_dir, "corpus.db"))

    app = Flask(__name__, static_folder=UI_DIR, static_url_path="")
    app.config["CORPUS"] = {"ws": ws, "db": db_path, "runner": runner or hermes_runner,
                            "client": client, "model": model_label or
                            (getattr(client, "model", None) or "demo-echo")}

    def ctx():
        return app.config["CORPUS"]

    def workspace_payload():
        status = ctx()["ws"].status()
        status["documents"] = _db.documents(ctx()["db"])
        return status

    # -- static ---------------------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(UI_DIR, "index.html")

    # -- same contract as ui_server.py ----------------------------------
    @app.get("/api/health")
    def health():
        c = ctx()
        return jsonify({"ok": True, "service": "desensitizer-core-vagrant-bootstrap",
                        "live": c["client"] is not None, "model": c["model"],
                        "kinds": [k for k, _ in __import__("censor").PATTERNS]
                                 + (["name"] if c["ws"].censors.names else []),
                        "names": len(c["ws"].censors.names), "version": VERSION,
                        "project": c["ws"].project, "db": c["db"]})

    @app.post("/api/censor")
    def api_censor():
        payload = request.get_json(silent=True) or {}
        text = payload.get("text") or ""
        with LOCK:
            censored, kinds = ctx()["ws"].censors.censor(text)
            ctx()["ws"].censors.save()
        tokens = [{"kind": k, "token": f"{k}:{t}"}
                  for k, t in __import__("censor").TOKEN_RE.findall(censored)]
        return jsonify({"censored": censored, "kinds": kinds, "tokens": tokens, "leaks": []})

    @app.post("/api/decensor")
    def api_decensor():
        text = (request.get_json(silent=True) or {}).get("text") or ""
        try:
            return jsonify({"restored": ctx()["ws"].restore(text)})
        except Exception as exc:  # CensorFailure
            return jsonify({"error": str(exc)}), 200

    @app.post("/api/run")
    def api_run():
        payload = request.get_json(silent=True) or {}
        text, mode = payload.get("text") or "", payload.get("mode", "demo")
        if not text.strip():
            return jsonify({"error": "nothing to send"}), 400
        c = ctx()
        if mode == "live":
            if c["client"] is None:
                return jsonify({"refused": {"stage": "config",
                                            "reason": "live mode is off; restart with --live and a key"}})
            client = c["client"]
        else:
            from ui_server import DemoTransport, _ClientWrapper
            client = _ClientWrapper(DemoTransport())
        try:
            result = pipeline.run(text, c["ws"].censors, client)
        except pipeline.RunRefused as exc:
            return jsonify({"refused": {"stage": exc.stage, "reason": exc.reason}})
        return jsonify({"ok": True, "censored": result.censored, "raw": result.raw,
                        "restored": result.restored, "kinds": result.kinds,
                        "elapsed_ms": result.elapsed_ms, "run_id": result.run_id,
                        "mode": mode, "model": result.model})

    @app.post("/api/upload")
    def api_upload():
        payload = request.get_json(silent=True) or {}
        name, data = payload.get("name") or "", payload.get("data") or ""
        if not data:
            return jsonify({"error": "no file data"}), 400
        try:
            raw = base64.b64decode(data, validate=True)
        except Exception:
            return jsonify({"error": "upload was not valid base64"}), 400
        suffix = os.path.splitext(name)[1].lower()
        if not _ingest.supported("x" + suffix):
            return jsonify({"error": f"unsupported format {suffix or '(none)'}"}), 400

        import tempfile
        allow_fragments = bool(payload.get("allow_fragments"))
        fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="upload-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
            with LOCK:
                entry = ctx()["ws"].add(tmp, allow_fragments=allow_fragments)
        except (_ingest.IngestFailure, _workspace.WorkspaceFailure) as exc:
            return jsonify({"error": str(exc)}), 400
        finally:
            os.unlink(tmp)
        entry["source_name"] = os.path.basename(name) or entry["source_name"]
        _db.add_document(ctx()["db"], entry)
        with LOCK:
            ctx()["ws"]._save_manifest()
            ctx()["ws"].write_index()
        return jsonify({"document": entry, "workspace": workspace_payload()})

    @app.get("/api/workspace")
    def api_workspace():
        return jsonify({"enabled": True, **workspace_payload()})

    @app.get("/api/index")
    def api_index():
        with open(ctx()["ws"].index_path, encoding="utf-8") as fh:
            return jsonify({"index": fh.read()})

    # -- database-backed integration ------------------------------------
    @app.get("/api/documents")
    def api_documents():
        return jsonify({"documents": _db.documents(ctx()["db"])})

    @app.get("/api/documents/<doc_id>/spans")
    def api_spans(doc_id):
        if not _db.document(ctx()["db"], doc_id):
            return jsonify({"error": f"no document {doc_id}"}), 404
        return jsonify({"doc_id": doc_id,
                        "spans": _db.spans(ctx()["db"], doc_id),
                        "fragments": _db.fragments(ctx()["db"], doc_id)})

    @app.post("/api/documents/<doc_id>/restore")
    def api_restore(doc_id):
        payload = request.get_json(silent=True) or {}
        fmt = (payload.get("fmt") or "md").lower()
        doc = _db.document(ctx()["db"], doc_id)
        if not doc:
            return jsonify({"error": f"no document {doc_id}"}), 404
        source = os.path.join(ctx()["ws"].dir, doc["aliased_path"])
        if payload.get("source"):
            source = os.path.join(ctx()["ws"].work_dir, os.path.basename(payload["source"]))
        try:
            entry = ctx()["ws"].restore_file(source, fmt=fmt)
        except (_workspace.WorkspaceFailure, _emit.EmitFailure) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # CensorFailure: unknown token
            return jsonify({"error": f"refused: {exc}"}), 400
        _db.add_restore(ctx()["db"], doc_id, entry)
        entry["download"] = f"/api/download/{os.path.basename(entry['restored'])}"
        return jsonify({"restore": entry})

    @app.get("/api/download/<path:name>")
    def api_download(name):
        restored_dir = os.path.realpath(ctx()["ws"].restored_dir)
        full = os.path.realpath(os.path.join(restored_dir, os.path.basename(name)))
        if not full.startswith(restored_dir + os.sep) or not os.path.isfile(full):
            abort(404)
        return send_from_directory(restored_dir, os.path.basename(full), as_attachment=True)

    @app.post("/api/jobs")
    def api_job_create():
        payload = request.get_json(silent=True) or {}
        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"error": "prompt is required"}), 400
        doc_ids = payload.get("doc_ids") or [d["id"] for d in _db.documents(ctx()["db"])]
        agent = payload.get("agent") or f"desens-{ctx()['ws'].project}"
        job_id = _db.create_job(ctx()["db"], prompt, agent, doc_ids, ctx()["ws"].work_dir)
        return jsonify({"job": _db.job(ctx()["db"], job_id)}), 201

    @app.get("/api/jobs")
    def api_jobs():
        return jsonify({"jobs": _db.jobs(ctx()["db"])})

    @app.get("/api/jobs/<job_id>")
    def api_job(job_id):
        row = _db.job(ctx()["db"], job_id)
        return (jsonify({"job": row}) if row else (jsonify({"error": "no such job"}), 404))

    @app.post("/api/jobs/<job_id>/run")
    def api_job_run(job_id):
        c = ctx()
        row = _db.job(c["db"], job_id)
        if not row:
            return jsonify({"error": "no such job"}), 404
        if row["status"] == "running":
            return jsonify({"error": "already running"}), 409

        def work():
            _db.set_job(c["db"], job_id, status="running", started_at=_db.now())
            try:
                result = c["runner"](row["agent"], row["prompt"], row["work_dir"])
                _db.set_job(c["db"], job_id, status="done" if result.get("exit_code") == 0 else "failed",
                            finished_at=_db.now(), exit_code=result.get("exit_code"),
                            output=result.get("output"), error=result.get("error"))
            except Exception as exc:
                _db.set_job(c["db"], job_id, status="failed", finished_at=_db.now(),
                            error=f"{type(exc).__name__}: {exc}")

        threading.Thread(target=work, daemon=True).start()
        return jsonify({"job": _db.job(c["db"], job_id), "accepted": True}), 202

    return app


def build_client(args):
    if not args.live:
        return None
    key = os.environ.get(args.api_key_env or "", "")
    if not key:
        raise SystemExit(f"app: --live needs an API key in ${args.api_key_env}")
    if not (args.base_url and args.model):
        raise SystemExit("app: --live needs --base-url and --model")
    return cloud.CloudClient(base_url=args.base_url, model=args.model, api_key=key, timeout=args.timeout)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Flask integration layer for the desensitised corpus")
    ap.add_argument("--project", required=True)
    ap.add_argument("--root", default=os.environ.get("DESENS_ROOT", os.path.expanduser("~/desensitizer")))
    ap.add_argument("--names", default=os.environ.get("VAGRANT_NAMES", ""))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8820)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--env-file", default=None)
    ap.add_argument("--api-key-env", default="VAGRANT_API_KEY")
    ap.add_argument("--base-url", default=os.environ.get("VAGRANT_BASE_URL"))
    ap.add_argument("--model", default=os.environ.get("VAGRANT_MODEL"))
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args(argv)

    if args.env_file:
        from ui_server import load_env_file
        load_env_file(args.env_file)
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    client = build_client(args)
    app = create_app(args.root, args.project, names=names, client=client,
                     model_label=args.model or (getattr(client, "model", None) or "demo-echo"))
    print(f"integration layer -> http://{args.host}:{args.port}/  (project {args.project})")
    print(f"  db: {app.config['CORPUS']['db']}")
    print(f"  work dir: {app.config['CORPUS']['ws'].work_dir}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
