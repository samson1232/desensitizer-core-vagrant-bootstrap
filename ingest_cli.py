#!/usr/bin/env python3
"""CLI for the desensitised corpus: add documents, refresh the index, restore replies.

    python3 ingest_cli.py --project acme add ticket.pdf notes.docx costs.xlsx
    python3 ingest_cli.py --project acme index
    python3 ingest_cli.py --project acme status
    python3 ingest_cli.py --project acme restore --in reply.txt --out restored.txt

The default root is $DESENS_ROOT or ~/desensitizer; pass --root to point elsewhere. Adding
a document extracts its text, aliases it with the project key, verifies the result is clean,
and only then writes it into work/.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ingest as _ingest  # noqa: E402
import workspace as _workspace  # noqa: E402


def open_ws(args, create=False):
    return _workspace.Workspace(args.root, args.project, names=args.names, create=create)


def cmd_add(args):
    ws = open_ws(args, create=True)
    rc = 0
    for path in args.files:
        try:
            entry = ws.add(path, allow_fragments=getattr(args, "allow_fragments", False))
        except (_ingest.IngestFailure, _workspace._censor.CensorFailure, OSError) as exc:
            print(f"  [FAIL] {os.path.basename(path)}: {exc}", file=sys.stderr)
            rc = 1
            continue
        kinds = ", ".join(f"{k}×{v}" for k, v in sorted(entry["kinds"].items())) or "no placeholders"
        flag = "  [NEEDS REVIEW]" if entry.get("status") == "needs_review" else ""
        print(f"  [{entry['id']}] {entry['source_name']} → {entry['aliased_path']}  ({kinds}){flag}")
    return rc


def cmd_index(args):
    ws = open_ws(args)
    ws.write_index()
    print(ws.index_path)
    return 0


def cmd_status(args):
    ws = open_ws(args)
    print(json.dumps(ws.status(), indent=2))
    return 0


def cmd_restore(args):
    ws = open_ws(args)
    text = sys.stdin.read() if args.infile in (None, "-") else open(args.infile, encoding="utf-8").read()
    try:
        restored = ws.restore(text)
    except _workspace._censor.CensorFailure as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if args.outfile in (None, "-"):
        sys.stdout.write(restored)
    else:
        tmp = f"{args.outfile}.tmp{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(restored)
        os.replace(tmp, args.outfile)
    return 0


def cmd_restore_doc(args):
    """Alias -> value for one whole file: the workflow that ends with a readable document."""
    ws = open_ws(args)
    source = args.file or os.path.join(ws.work_dir, f"{args.doc}.md")
    try:
        entry = ws.restore_file(source, fmt=args.fmt, out_dir=args.out_dir)
    except (_workspace.WorkspaceFailure, _workspace._censor.CensorFailure) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"  {entry['source']} → {entry['restored']}  ({entry['tokens']} token(s), {entry['format']})")
    return 0


def cmd_restore_all(args):
    ws = open_ws(args)
    rc = 0
    for entry in ws.restore_all(fmt=args.fmt):
        print(f"  {entry['source']} → {entry['restored']}  ({entry['tokens']} token(s))")
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(description="Manage a desensitised document corpus.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--root", default=os.environ.get("DESENS_ROOT", os.path.expanduser("~/desensitizer")))
    ap.add_argument("--names", nargs="*", default=[])
    sub = ap.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add", help="alias documents into work/")
    add.add_argument("files", nargs="+")
    add.add_argument("--allow-fragments", action="store_true",
                     help="ship a document whose extracted text looks clipped, marked needs_review")
    add.set_defaults(func=cmd_add)

    sub.add_parser("index", help="refresh work/INDEX.md").set_defaults(func=cmd_index)
    sub.add_parser("status", help="documents, kinds and paths").set_defaults(func=cmd_status)

    restore = sub.add_parser("restore", help="turn placeholders back into values (snippet)")
    restore.add_argument("--in", dest="infile", default="-")
    restore.add_argument("--out", dest="outfile", default="-")
    restore.set_defaults(func=cmd_restore)

    doc = sub.add_parser("restore-doc", help="alias -> value for one whole file")
    doc.add_argument("--doc", required=False, help="document id, e.g. doc-001 (reads work/<id>.md)")
    doc.add_argument("--file", default=None, help="explicit aliased file (defaults to work/<id>.md)")
    doc.add_argument("--fmt", default="md", help="output format: md, txt, docx, xlsx (default md)")
    doc.add_argument("--out-dir", default=None, help="output directory (default <project>/restored)")
    doc.set_defaults(func=cmd_restore_doc)

    allp = sub.add_parser("restore-all", help="restore every .md in work/ into restored/")
    allp.add_argument("--fmt", default="md")
    allp.set_defaults(func=cmd_restore_all)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
