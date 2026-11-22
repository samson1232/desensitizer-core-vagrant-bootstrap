#!/usr/bin/env python3
"""Agentless decensor. A script, not a model, not a loop, not an agent.

    python3 decensor.py --vault vault.json --key desens_key.bin --in reply.txt
    python3 decensor.py --vault vault.json --key desens_key.bin --check --in reply.txt
    cat reply.txt | python3 decensor.py --vault vault.json --key desens_key.bin

What "agentless" means here, concretely, and what ``tests/test_decensor.py``
enforces by parsing this file's imports:

* the only modules it can import are the standard library and ``censor``;
* ``censor`` itself imports only the standard library;
* there is no HTTP client, no model call, no subprocess, and no tool loop.

Restoring a token is a dictionary lookup and a string substitution. It is
deterministic: the same input and vault always produce the same bytes out.

Exit codes: 0 restored (or --check passed), 2 refused (unknown token, bad vault,
wrong key). On refusal nothing is written — a half-restored document is worse
than no document.
"""
import argparse
import os
import sys

import censor


def _read(path):
    if path in (None, "-"):
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _write(path, text):
    if path in (None, "-"):
        sys.stdout.write(text)
        return
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Restore kind:TOKEN placeholders to their values.")
    ap.add_argument("--vault", required=True, help="vault.json written by the censor pass")
    ap.add_argument("--key", required=True, help="key file (mode 0600) that produced the tokens")
    ap.add_argument("--in", dest="infile", default="-", help="censored text file, or - for stdin")
    ap.add_argument("--out", dest="outfile", default="-", help="output file, or - for stdout")
    ap.add_argument("--check", action="store_true", help="verify every token resolves; write nothing")
    ap.add_argument("--stats", action="store_true", help="print token counts by kind to stderr")
    args = ap.parse_args(argv)

    try:
        text = _read(args.infile)
    except OSError as exc:
        print(f"decensor: cannot read {args.infile}: {exc}", file=sys.stderr)
        return 2

    try:
        # create_key=False: a missing key is a refusal, never a new key. Minting one
        # here would silently make every stored token unresolvable.
        cens = censor.Censor(args.key, args.vault, create_key=False)
    except censor.CensorFailure as exc:
        print(f"decensor: {exc}", file=sys.stderr)
        return 2

    tokens = censor.TOKEN_RE.findall(text)
    if args.stats:
        counts = {}
        for kind, _tok in tokens:
            counts[kind] = counts.get(kind, 0) + 1
        print("tokens: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"), file=sys.stderr)

    try:
        restored = cens.restore(text)
    except censor.CensorFailure as exc:
        print(f"decensor: refused: {exc}", file=sys.stderr)
        return 2

    if censor.TOKEN_RE.search(restored):
        print("decensor: refused: tokens remain after restore", file=sys.stderr)
        return 2

    if args.check:
        print(f"ok: {len(tokens)} token(s) resolve; nothing written", file=sys.stderr)
        return 0

    _write(args.outfile, restored)
    return 0


if __name__ == "__main__":
    sys.exit(main())
