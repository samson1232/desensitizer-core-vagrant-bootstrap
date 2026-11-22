"""Controls for the agentless decensor: CLI behaviour, and the import graph.

The import test is the one that matters. "Agentless" is not a claim about the
design, it is a property of the file: if decensor.py cannot import an HTTP client,
a model SDK or a subprocess module, then it cannot reach a model or an agent — and
the test fails the moment someone adds one.
"""
import ast
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import censor  # noqa: E402

DECENSOR = os.path.join(ROOT, "decensor.py")
FAILS = []


def chk(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)
    return ok


def imports_of(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def run(args, stdin=None):
    return subprocess.run([sys.executable, DECENSOR] + args, input=stdin,
                          capture_output=True, text=True, cwd=ROOT)


def main():
    allowed = {"argparse", "os", "sys", "censor"}
    found = imports_of(DECENSOR)
    chk("imports/decensor-allowlist", found <= allowed, f"unexpected: {sorted(found - allowed)}")
    for banned in ("urllib", "http", "socket", "subprocess", "requests", "openai", "anthropic"):
        chk(f"imports/decensor-no-{banned}", banned not in found)

    censor_imports = imports_of(os.path.join(ROOT, "censor.py"))
    stdlib_only = {"base64", "hashlib", "hmac", "json", "os", "re", "secrets"}
    chk("imports/censor-stdlib-only", censor_imports <= stdlib_only, f"unexpected: {sorted(censor_imports - stdlib_only)}")

    tmp = tempfile.mkdtemp(prefix="dectest")
    try:
        key = os.path.join(tmp, "key.bin")
        vault = os.path.join(tmp, "vault.json")
        original = "Dear Alice Smith <alice.smith@example.com>,\n\tticket STF-4821 — café ✓\nline two."
        c = censor.Censor(key, vault)
        censored, _ = c.censor_verified(original)
        c.save()

        src = os.path.join(tmp, "in.txt")
        dst = os.path.join(tmp, "out.txt")
        open(src, "w", encoding="utf-8").write(censored)

        r = run(["--vault", vault, "--key", key, "--in", src, "--out", dst])
        chk("cli/restores-byte-exact", r.returncode == 0 and open(dst, encoding="utf-8").read() == original,
            f"rc={r.returncode} {r.stderr[:120]}")

        bad = os.path.join(tmp, "bad.txt")
        open(bad, "w", encoding="utf-8").write("unknown email:AAAAAAAAAAAAAAAAAAAAAAAAAA here")
        out2 = os.path.join(tmp, "never.txt")
        r = run(["--vault", vault, "--key", key, "--in", bad, "--out", out2])
        chk("cli/unknown-token-refused", r.returncode == 2 and not os.path.exists(out2),
            f"rc={r.returncode} exists={os.path.exists(out2)}")

        chk("cli/check-mode", run(["--vault", vault, "--key", key, "--in", src, "--check", "--out", dst + "2"]).returncode == 0
            and not os.path.exists(dst + "2"))

        r = run(["--vault", vault, "--key", key, "--in", src, "--out", dst, "--stats"])
        chk("cli/stats", "email=1" in r.stderr, r.stderr[:120])

        r = run(["--vault", vault, "--key", key, "--out", "-"], stdin=censored)
        chk("cli/stdin-stdout", r.returncode == 0 and r.stdout == original, f"rc={r.returncode}")

        r = run(["--vault", vault, "--key", os.path.join(tmp, "absent.bin"), "--in", src, "--out", dst])
        chk("cli/missing-key-refused", r.returncode == 2, f"rc={r.returncode}")

        other_key = os.path.join(tmp, "other.bin")
        censor.load_or_create_key(other_key)
        r = run(["--vault", vault, "--key", other_key, "--in", src, "--out", dst])
        chk("cli/wrong-key-refused", r.returncode == 2, f"rc={r.returncode}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all decensor controls passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
