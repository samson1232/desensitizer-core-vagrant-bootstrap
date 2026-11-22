#!/usr/bin/env python3
"""Per-word censoring decisions from a Kev endpoint, scored against this repo's detector.

    probe.py --url http://127.0.0.1:8008 --mode score   # Protocol A: word as state, censor question
    probe.py --url http://127.0.0.1:8008 --mode names   # Protocol D: "is this a person's name?"
    probe.py --url http://127.0.0.1:8010 --mode time    # timing only, no decisions collected

Results and the rejected variants are in docs/kev-per-word-eval.md. Labels come from
censor._collect with the roster, computed locally; the model only ever sees one word.
"""
import argparse
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import censor  # noqa: E402

CENSOR_Q = "Does this text identify a specific person or a specific device?"
NAME_Q = "Is this word a person's name?"
ROSTER = ["Priya Nair"]


def call(url, state, question, timeout=300):
    body = json.dumps({"state": state, "questions": {"q": {"type": "noul", "instructions": question}}}).encode()
    req = urllib.request.Request(f"{url.rstrip('/')}/v1/systemone", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())["answers"]["q"]["noul"]


def words_with_labels(doc):
    spans = [(s["start"], s["end"], s["kind"])
             for s in censor._collect(doc, censor.compile_roster(ROSTER)) if s["restorable"]]
    out = []
    for m in re.finditer(r"\S+", doc):
        bare = m.group(0).strip(".,;:()[]\"'")
        if bare:
            kind = next((k for a, b, k in spans if m.start() < b and m.end() > a), "")
            out.append({"word": bare, "label": kind})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8008")
    ap.add_argument("--mode", choices=("score", "names", "time"), default="score")
    ap.add_argument("--doc", default=os.path.join(HERE, "helpdesk_note.txt"))
    ap.add_argument("--threshold", type=float, default=0.5)
    args = ap.parse_args()

    doc = open(args.doc, encoding="utf-8").read()
    words = words_with_labels(doc)

    if args.mode == "time":
        t0 = time.perf_counter()
        for w in words:
            call(args.url, w["word"], CENSOR_Q)
        total = time.perf_counter() - t0
        print(f"# {args.url}  words={len(words)}  TOTAL {total:.2f}s  mean {total / len(words) * 1000:.0f} ms/word")
        return 0

    question = NAME_Q if args.mode == "names" else CENSOR_Q
    rows, lat = [], []
    for w in words:
        t0 = time.perf_counter()
        p = call(args.url, w["word"], question)
        lat.append(time.perf_counter() - t0)
        rows.append((w["word"], w["label"], p))

    tp = fp = fn = tn = 0
    for _word, label, p in rows:
        called, truth = p >= args.threshold, bool(label)
        tp += called and truth
        fp += called and not truth
        fn += truth and not called
        tn += (not called) and (not truth)

    print(f"# {args.url}  mode={args.mode}  words={len(words)}  mean latency {statistics.mean(lat) * 1000:.0f} ms")
    print(f"{'word':<26} {'truth':<7} {'p':>7}  call")
    for word, label, p in rows:
        print(f"{word[:26]:<26} {label or '-':<7} {p:>7.3f}  {'YES' if p >= args.threshold else '.'}")
    print(f"\naccuracy {(tp + tn) / max(len(rows), 1):.3f}  precision {tp / max(tp + fp, 1):.3f}  "
          f"recall {tp / max(tp + fn, 1):.3f}  tp={tp} fp={fp} fn={fn} tn={tn}")
    print("missed:", ", ".join(w for w, l, p in rows if l and p < args.threshold))
    print("needless:", ", ".join(w for w, l, p in rows if not l and p >= args.threshold) or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
