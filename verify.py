"""Single-command verification. Offline by default.

    python3 verify.py            # every offline control, no models, no network
    python3 verify.py --json     # machine-readable summary

Exit 0 only if every suite passed. Each suite prints one [PASS]/[FAIL] line per
control; the runner counts them so a suite that silently stops asserting shows up
as a missing count rather than a green run.
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
    "test_ui.py",
]
# Floor, not a target: raise it when you add controls, never lower it.
MIN_CHECKS = {"test_censor.py": 20, "test_cloud.py": 11, "test_decensor.py": 9,
              "test_pipeline.py": 9, "test_ui.py": 12}


def run_suite(name):
    path = os.path.join(HERE, "tests", name)
    proc = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=600, cwd=HERE)
    out = (proc.stdout or "") + (proc.stderr or "")
    return {
        "suite": name,
        "rc": proc.returncode,
        "pass": out.count("[PASS]"),
        "fail": out.count("[FAIL]"),
        "tail": [ln for ln in out.strip().splitlines() if ln.startswith("[FAIL]")][:6],
        "raw_tail": out.strip().splitlines()[-1] if out.strip() else "",
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    results, ok = [], True
    for name in SUITES:
        r = run_suite(name)
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
    print(f"\n  offline: {total} pass, {failed} fail")
    print("  VERIFY OK" if ok else "  VERIFY FAILED")
    if args.json:
        print(json.dumps({"ok": ok, "total_pass": total, "total_fail": failed, "suites": results}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
