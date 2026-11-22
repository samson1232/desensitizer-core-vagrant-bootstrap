#!/usr/bin/env python3
"""Build the two-agent setup on a fresh Hermes install.

    python3 bootstrap.py --project acme --dry-run     # print the plan, touch nothing
    python3 bootstrap.py --project acme               # do it
    python3 bootstrap.py --project acme --verify      # re-check an existing setup

What it creates:

    <root>/<project>/work/        the ONLY directory the worker agent may touch
    <root>/<project>/private/     originals (0700), never given to an agent
    <root>/<project>/state/       key + vault + manifest (0700), never given to an agent

    profile desens-<project>            worker  — cloud model, cwd = work/, confined by SOUL.md
    profile desens-<project>-curator    curator — cloud model, owns the project dir and the tools

The worker's confinement is three things stacked: `terminal.cwd` points at work/, SOUL.md
tells it the directory is its whole world, and — the part that actually holds — the real
values are not in that directory at all. This script does not sandbox processes; see
SECURITY.md for what would.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import workspace as _workspace  # noqa: E402

DISABLED_TOOLSETS = ["web", "browser", "image_gen", "video_gen", "tts", "stt"]


class Step:
    def __init__(self, name, ok, detail, fatal=False):
        self.name, self.ok, self.detail, self.fatal = name, ok, detail, fatal

    def as_dict(self):
        return {"step": self.name, "ok": self.ok, "detail": self.detail}

    def line(self):
        mark = "ok  " if self.ok else ("FAIL" if self.fatal else "warn")
        return f"  [{mark}] {self.name:32} {self.detail}"


HERMES_HOME_OVERRIDE = None


def hermes_home():
    return HERMES_HOME_OVERRIDE or os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def curator_soul(project, project_dir, work_dir, state_dir, model_label):
    return f"""# SOUL.md — desensitised corpus curator ({project})

## Identity

You keep the `{project}` corpus honest. You are the agent that adds documents, aliases them,
refreshes the index, and restores a reply when someone asks for the real values back. You do
not do the analytical work on the documents; that is the worker's job, in `{work_dir}`.

You run on {model_label}.

## Where you may work

    {project_dir}

Inside it:

- `private/` — originals as uploaded. Mode 0700. Read only when ingesting; never quote, copy
  or summarise their contents anywhere except through the aliaser.
- `work/` — aliased copies. Safe to read, diff, and hand to another agent.
- `state/` — key, vault, manifest. Never print the vault, never paste values from it into a
  message, a summary, or a file in `work/`.

Never work outside `{project_dir}`. If something needs to happen elsewhere, say so and stop.

## The rules that make this safe

1. **Alias first, always.** A file enters `work/` only through `workspace.add()`, which
   extracts, censors, verifies the result is clean, then writes. No hand-copying.
2. **Fail closed.** If the post-censor scan is not empty, the document does not ship. If a
   PDF has no text layer, report that; do not ship an empty page.
3. **The vault never leaves `state/`.** Not in chat, not in a summary, not in `work/`.
4. **Restore on request, one value at a time.** File-level restore goes back to the person who
   asked; it is not a bulk export.
5. **Report by id.** `doc-003`, `email ×2`, `phone ×1` — counts and identifiers, not values.

## Commands you own

```sh
python3 ingest_cli.py --project {project} add <file>      # alias one document into work/
python3 ingest_cli.py --project {project} index           # refresh work/INDEX.md
python3 ingest_cli.py --project {project} restore --in reply.txt --out restored.txt
python3 ingest_cli.py --project {project} status          # documents, kinds, paths
```

If a step fails, say which step and why. Do not invent a document that is not in `private/`.
"""


def worker_soul(project, work_dir, model_label):
    return _workspace.soul_for(work_dir, project, model_label).replace(
        "## Where you are", "## Where you are", 1)


def run(cmd, dry_run=False, env=None):
    if dry_run:
        return 0, f"(dry-run) would run: {' '.join(shlex_quote(c) for c in cmd)}", ""
    # Pin HERMES_HOME for every call: an inherited value would otherwise scatter the new
    # profiles into whatever profile this shell happens to be running under.
    merged = {**os.environ, "HERMES_HOME": hermes_home()}
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=merged)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def shlex_quote(part):
    return part if all(ch.isalnum() or ch in "-_./=:@[]\"" for ch in part) else f"'{part}'"


def profile_dir(name):
    return os.path.join(hermes_home(), "profiles", name)


def write_soul(profile_name, content, dry_run=False):
    path = os.path.join(profile_dir(profile_name), "SOUL.md")
    if dry_run:
        return Step(f"soul/{profile_name}", True, f"(dry-run) would write {path}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return Step(f"soul/{profile_name}", True, path)


def create_profile(hermes, name, description, clone, dry_run):
    code, out, err = run([hermes, "profile", "create", name, "--no-alias",
                          "--description", description] + (["--clone"] if clone else []), dry_run)
    if code == 0:
        return Step(f"profile/{name}", True, description)
    if os.path.isdir(profile_dir(name)):
        # Re-running bootstrap on an existing setup is normal; the profile is the thing
        # we wanted, and overwriting SOUL.md below is idempotent.
        return Step(f"profile/{name}", True, "already exists")
    tail = err.splitlines()[-1][:140] if err else f"exit {code}"
    return Step(f"profile/{name}", False, f"create failed: {tail}", fatal=True)


def config_set(hermes, profile, key, value, dry_run):
    code, out, err = run([hermes, "-p", profile, "config", "set", key, value], dry_run)
    return Step(f"config/{profile}/{key}", code == 0,
                (out or err or "").splitlines()[-1][:120] if (out or err) else f"set to {value}",
                fatal=key == "terminal.cwd")


def config_get(hermes, profile, key):
    code, out, err = run([hermes, "-p", profile, "config", "get", key])
    return out.strip() if code == 0 else ""


def build(args, steps):
    hermes = args.hermes_bin or shutil.which("hermes")
    if not hermes:
        steps.append(Step("hermes/binary", False, "no `hermes` on PATH; pass --hermes-bin", fatal=True))
        return steps
    steps.append(Step("hermes/binary", True, hermes))

    if not args.dry_run:
        try:
            ws = _workspace.Workspace(args.root, args.project, create=True, names=args.names)
        except _workspace.WorkspaceFailure as exc:
            steps.append(Step("workspace", False, str(exc), fatal=True))
            return steps
    else:
        ws = None
    ws_dir = os.path.join(os.path.abspath(args.root), args.project)
    work_dir = os.path.join(ws_dir, "work")
    steps.append(Step("workspace", True, ws_dir))

    worker = f"desens-{args.project}"
    curator = f"{worker}-curator"

    for name, desc in ((worker, f"Aliased worker for the {args.project} corpus; confined to {work_dir}"),
                       (curator, f"Corpus curator for {args.project}; owns ingest, index and restore")):
        steps.append(create_profile(hermes, name, desc, args.clone, args.dry_run))

    steps.append(write_soul(worker, worker_soul(args.project, work_dir, args.model), args.dry_run))
    steps.append(write_soul(curator, curator_soul(args.project, ws_dir, work_dir,
                                                  os.path.join(ws_dir, "state"), args.model), args.dry_run))

    steps.append(config_set(hermes, worker, "terminal.cwd", work_dir, args.dry_run))
    steps.append(config_set(hermes, curator, "terminal.cwd", ws_dir, args.dry_run))
    for name in (worker, curator):
        steps.append(config_set(hermes, name, "agent.disabled_toolsets",
                                json.dumps(DISABLED_TOOLSETS), args.dry_run))

    if ws is not None:
        soul_path = os.path.join(ws_dir, "SOUL.md")
        with open(soul_path, "w", encoding="utf-8") as fh:
            fh.write(worker_soul(args.project, work_dir, args.model))
        ws.write_index()
        steps.append(Step("workspace/soul", True, soul_path))
        steps.append(Step("workspace/index", True, ws.index_path))
    return steps


def verify(steps, args):
    hermes = args.hermes_bin or shutil.which("hermes")
    worker = f"desens-{args.project}"
    ws_dir = os.path.join(os.path.abspath(args.root), args.project)
    work_dir = os.path.join(ws_dir, "work")

    leaf = os.path.join(profile_dir(worker), "SOUL.md")
    if os.path.exists(leaf):
        text = open(leaf, encoding="utf-8").read()
        steps.append(Step("verify/worker-soul", work_dir in text, "soul names the work dir"))
    else:
        steps.append(Step("verify/worker-soul", False, f"missing {leaf}", fatal=True))

    if args.dry_run:
        steps.append(Step("verify/cwd", True, "(dry-run) skipped"))
    else:
        got = config_get(hermes, worker, "terminal.cwd") if hermes else ""
        steps.append(Step("verify/cwd", os.path.realpath(got) == os.path.realpath(work_dir),
                          got or "unset", fatal=True))

    steps.append(Step("verify/work-dir", os.path.isdir(work_dir), work_dir, fatal=True))
    private = os.path.join(ws_dir, "private")
    if os.path.isdir(private):
        mode = os.stat(private).st_mode & 0o777
        steps.append(Step("verify/private-mode", mode == 0o700, oct(mode)))
    return steps


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the two-agent desensitised-document setup.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--root", default=os.environ.get("DESENS_ROOT", os.path.expanduser("~/desensitizer")))
    ap.add_argument("--model", default="the configured cloud model")
    ap.add_argument("--names", nargs="*", default=[], help="name roster to alias")
    ap.add_argument("--hermes-bin", default=None)
    ap.add_argument("--hermes-home", default=None,
                    help="Hermes home to create the profiles in (default: $HERMES_HOME or ~/.hermes)")
    ap.add_argument("--no-clone", dest="clone", action="store_false",
                    help="do not copy model config/credentials from the active profile")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true", help="only re-check an existing setup")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.hermes_home:
        global HERMES_HOME_OVERRIDE
        HERMES_HOME_OVERRIDE = os.path.abspath(os.path.expanduser(args.hermes_home))

    steps = []
    steps = verify(steps, args) if args.verify else build(args, steps)
    if not args.verify and not args.dry_run:
        steps = verify(steps, args)

    ok = all(s.ok for s in steps)
    if args.json:
        print(json.dumps({"ok": ok, "root": os.path.join(args.root, args.project),
                          "steps": [s.as_dict() for s in steps]}, indent=2))
    else:
        print(f"bootstrap: project {args.project}  ({'dry run' if args.dry_run else 'applied'})")
        for s in steps:
            print(s.line())
        ws_dir = os.path.join(os.path.abspath(args.root), args.project)
        print()
        print(f"  workspace : {ws_dir}")
        print(f"  worker    : hermes -p desens-{args.project} chat      (cwd {os.path.join(ws_dir, 'work')})")
        print(f"  curator   : hermes -p desens-{args.project}-curator chat")
        print()
        print("  add a document:  python3 ingest_cli.py --project "
              f"{args.project} --root {os.path.abspath(args.root)} add <file>")
        print(f"  {'OK' if ok else 'SOMETHING FAILED — read the FAIL lines above'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
