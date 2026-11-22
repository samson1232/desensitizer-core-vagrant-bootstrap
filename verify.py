"""Single-command verification. Offline by default.

    python3 verify.py            # every offline control, no models, no network
    python3 verify.py --json     # machine-readable summary
    python3 verify.py --strict   # a skipped suite is a failure (CI uses this)

Exit 0 only if every runnable suite passed and no suite fell under its check floor. Each
suite prints one [PASS]/[FAIL] line per control; the runner counts them, so a suite that
silently stops asserting shows up as a missing count rather than a green run.

The Flask integration suite needs Flask. It runs under .venv/bin/python (or $DESENS_PYTHON)
when that interpreter can import Flask, and reports SKIP otherwise — a skip you can see,
never a pass you cannot trust.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = [
    "test_censor.py",
    "test_cloud.py",
    "test_decensor.py",
    "test_pipeline.py",
    "test_ingest.py",
    "test_workspace.py",
    "test_sample_note.py",
    "test_damaged_text.py",
    "test_bootstrap.py",
    "test_ui.py",
    "test_app.py",
]
# Floor, not a target: raise it when you add controls, never lower it.
MIN_CHECKS = {
    "test_censor.py": 20,
    "test_cloud.py": 11,
    "test_decensor.py": 9,
    "test_pipeline.py": 9,
    "test_ingest.py": 9,
    "test_workspace.py": 15,
    "test_sample_note.py": 16,
    "test_damaged_text.py": 19,
    "test_bootstrap.py": 12,
    "test_ui.py": 12,
    "test_app.py": 15,
}
# Suites that need a third-party package; value is the import name to probe for.
NEEDS = {"test_app.py": "flask"}


def python_for(suite):
    """An interpreter that can import the suite's dependency, or None if there is none."""
    need = NEEDS.get(suite)
    if not need:
        return sys.executable
    for exe in (os.environ.get("DESENS_PYTHON"), os.path.join(HERE, ".venv", "bin", "python"), sys.executable):
        if not exe or not os.path.exists(exe):
            continue
        if subprocess.run([exe, "-c", f"import {need}"], capture_output=True).returncode == 0:
            return exe
    return None


def run_suite(name, python_exe):
    path = os.path.join(HERE, "tests", name)
    proc = subprocess.run([python_exe, path], capture_output=True, text=True, timeout=900, cwd=HERE)
    out = (proc.stdout or "") + (proc.stderr or "")
    return {
        "suite": name,
        "rc": proc.returncode,
        "pass": out.count("[PASS]"),
        "fail": out.count("[FAIL]"),
        "tail": [ln for ln in out.strip().splitlines() if ln.startswith("[FAIL]")][:6],
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    results, ok, skipped = [], True, []
    for name in SUITES:
        python_exe = python_for(name)
        if python_exe is None:
            skipped.append(name)
            print(f"  [SKIP] {name:20} needs '{NEEDS[name]}' — uv pip install -r requirements.txt")
            ok = ok and not args.strict
            continue
        r = run_suite(name, python_exe)
        floor = MIN_CHECKS.get(name, 0)
        too_few = r["pass"] < floor
        good = r["rc"] == 0 and r["fail"] == 0 and not too_few
        r["ok"] = good
        ok = ok and good
        results.append(r)
        note = "" if not too_few else f"  (only {r['pass']} checks, expected >= {floor})"
        print(f"  [{'PASS' if good else 'FAIL'}] {name:20} {r['pass']} pass, {r['fail']} fail{note}")
        for line in r["tail"]:
            print(f"         {line}")

    total = sum(r["pass"] for r in results)
    failed = sum(r["fail"] for r in results)
    suffix = f", {len(skipped)} skipped" if skipped else ""
    print(f"\n  offline: {total} pass, {failed} fail{suffix}")
    print("  VERIFY OK" if ok else "  VERIFY FAILED")
    if args.json:
        print(json.dumps({"ok": ok, "total_pass": total, "total_fail": failed,
                          "skipped": skipped, "suites": results}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
