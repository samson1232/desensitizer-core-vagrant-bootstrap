"""Controls for bootstrap: does it really build two confined profiles, without a real Hermes?

The stub on PATH behaves like `hermes` for exactly the three calls bootstrap makes
(`profile create`, `config set`, `config get`) so the suite can assert the shape of the
setup without touching the machine's real profiles.
"""
import json
import os
import stat
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bootstrap  # noqa: E402

STUB = '''#!/usr/bin/env python3
import json, os, sys
home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
args = sys.argv[1:]
def cfg(profile):
    return os.path.join(home, "profiles", profile, "_cfg.json")
if args[:2] == ["profile", "create"]:
    name = args[2]
    d = os.path.join(home, "profiles", name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "config.yaml"), "w").write("model: {}\\n")
    print("created", name)
    sys.exit(0)
if args and args[0] == "-p":
    profile, rest = args[1], args[2:]
    if rest[:2] == ["config", "set"] and len(rest) >= 4:
        key, value = rest[2], rest[3]
        data = {}
        if os.path.exists(cfg(profile)):
            data = json.load(open(cfg(profile)))
        data[key] = value
        json.dump(data, open(cfg(profile), "w"))
        print(f"{key} = {value}")
        sys.exit(0)
    if rest[:2] == ["config", "get"] and len(rest) >= 3:
        data = json.load(open(cfg(profile))) if os.path.exists(cfg(profile)) else {}
        print(data.get(rest[2], ""))
        sys.exit(0)
print("stub: unsupported", args, file=sys.stderr)
sys.exit(2)
'''

FAILS = []


def chk(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)
    return ok


def main():
    tmp = tempfile.mkdtemp(prefix="boottest")
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    stub = os.path.join(tmp, "hermes-stub")
    with open(stub, "w") as fh:
        fh.write(STUB)
    os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    old_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = home
    try:
        root = os.path.join(tmp, "corpus")
        rc = bootstrap.main(["--project", "acme", "--root", root, "--hermes-bin", stub])
        chk("run/exit-0", rc == 0, f"rc={rc}")

        worker = os.path.join(home, "profiles", "desens-acme")
        curator = os.path.join(home, "profiles", "desens-acme-curator")
        chk("profiles/worker-created", os.path.isdir(worker))
        chk("profiles/curator-created", os.path.isdir(curator))

        work_dir = os.path.join(root, "acme", "work")
        soul = open(os.path.join(worker, "SOUL.md"), encoding="utf-8").read()
        chk("soul/names-work-dir", work_dir in soul, soul[:80])
        chk("soul/forbids-leaving", "Do not read, list, write or glob outside it" in soul)
        chk("soul/forbids-recovery", "Do not try to recover, guess, infer or reconstruct" in soul)
        chk("soul/curator-owns-ingest",
            "python3 ingest_cli.py" in open(os.path.join(curator, "SOUL.md"), encoding="utf-8").read())

        cfg = json.load(open(os.path.join(worker, "_cfg.json")))
        chk("config/cwd-is-work-dir", os.path.realpath(cfg["terminal.cwd"]) == os.path.realpath(work_dir), cfg.get("terminal.cwd"))
        chk("config/toolsets-restricted", "web" in cfg.get("agent.disabled_toolsets", ""), cfg.get("agent.disabled_toolsets"))

        chk("workspace/dirs", all(os.path.isdir(os.path.join(root, "acme", d))
                                  for d in ("work", "private", "state")))
        chk("workspace/soul-copy", os.path.isfile(os.path.join(root, "acme", "SOUL.md")))

        rc2 = bootstrap.main(["--project", "acme", "--root", root, "--hermes-bin", stub, "--verify"])
        chk("verify/re-check-ok", rc2 == 0, f"rc={rc2}")

        rc3 = bootstrap.main(["--project", "beta", "--root", root, "--hermes-bin", stub, "--dry-run"])
        chk("dry-run/exit-0", rc3 == 0)
        chk("dry-run/creates-nothing", not os.path.exists(os.path.join(home, "profiles", "desens-beta")))

        rc4 = bootstrap.main(["--project", "acme", "--root", root, "--hermes-bin", stub])
        chk("rerun/idempotent", rc4 == 0, f"rc={rc4}")
    finally:
        if old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old_home
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all bootstrap controls passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
