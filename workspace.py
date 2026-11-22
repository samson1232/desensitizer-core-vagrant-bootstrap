"""The desensitised repository: a directory tree an agent can work in safely.

Layout for one project:

    <root>/<project>/
        private/            originals as uploaded, mode 0700 — never handed to an agent
        work/               aliased copies only; this is the agent's entire world
            INDEX.md        what is here, kinds and counts — no values
            doc-001.md      aliased text
        state/
            key.bin         0600
            vault.json      0600  token -> value
            manifest.json   doc id -> original name, kinds, sizes (no values)
        SOUL.md             the worker's confinement, generated per work dir

The point of the split: the values a document contains exist only in `private/` and
`state/`. `work/` can be handed to a model and to an agent without leaking them, and a
restore needs `state/`, which is not in the agent's directory.

This module is model-free and agent-free; the only thing it calls out to is `censor`.
"""
import datetime
import hashlib
import json
import os
import re
import shutil

import censor as _censor
import ingest as _ingest

WORK = "work"
PRIVATE = "private"
STATE = "state"
RESTORED = "restored"
INDEX = "INDEX.md"
MANIFEST = "manifest.json"


class WorkspaceFailure(Exception):
    """The workspace cannot be used as asked. Never a partial result."""


HEADER_MARK = "<!-- desensitized:"


def _strip_generated_header(text):
    """Drop the banner `add()` writes, so a restored file is the document, not the wrapper."""
    if not text.startswith(HEADER_MARK):
        return text
    marker = "\n---\n\n"
    index = text.find(marker)
    return text[index + len(marker):] if index != -1 else text


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _slug(name):
    stem = os.path.splitext(os.path.basename(name))[0]
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    return slug[:40] or "document"


class Workspace:
    def __init__(self, root, project, names=None, create=False):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", project or ""):
            raise WorkspaceFailure("project must be lowercase alphanumeric with - or _ (max 41 chars)")
        self.root = os.path.abspath(root)
        self.project = project
        self.dir = os.path.join(self.root, project)
        self.work_dir = os.path.join(self.dir, WORK)
        self.private_dir = os.path.join(self.dir, PRIVATE)
        self.state_dir = os.path.join(self.dir, STATE)
        self.index_path = os.path.join(self.work_dir, INDEX)
        self.manifest_path = os.path.join(self.state_dir, MANIFEST)

        if not os.path.isdir(self.dir):
            if not create:
                raise WorkspaceFailure(f"no workspace at {self.dir}; run with --create")
            self._create()

        self.censors = _censor.Censor(os.path.join(self.state_dir, "key.bin"),
                                      os.path.join(self.state_dir, "vault.json"), names=names)
        self.manifest = self._load_manifest()

    # -- creation ---------------------------------------------------------
    def _create(self):
        for path in (self.dir, self.work_dir, self.private_dir, self.state_dir):
            os.makedirs(path, exist_ok=True)
        os.chmod(self.private_dir, 0o700)
        os.chmod(self.state_dir, 0o700)
        self.manifest = {"project": self.project, "created": _now(), "documents": []}
        self._save_manifest()
    def _load_manifest(self):
        if not os.path.exists(self.manifest_path):
            return {"project": self.project, "created": _now(), "documents": []}
        with open(self.manifest_path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _save_manifest(self):
        os.makedirs(self.state_dir, exist_ok=True)
        tmp = f"{self.manifest_path}.tmp{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.manifest, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, self.manifest_path)

    # -- documents --------------------------------------------------------
    def next_id(self):
        used = {d["id"] for d in self.manifest["documents"]}
        n = 1
        while f"doc-{n:03d}" in used:
            n += 1
        return f"doc-{n:03d}"

    def add(self, source_path, keep_original=True, allow_fragments=False):
        """Ingest one document, alias it, write the work copy and the manifest entry.

        Extraction is lossy, so the span record keeps how each value was found (strict or
        loose) and what could not be restored (fragments). A document with fragments is
        refused unless ``allow_fragments`` is set, in which case it ships marked
        ``needs_review`` — the difference between "aliased" and "aliased but suspect" is
        in the manifest and the database, not in a console message nobody reads.
        """
        text, fmt = _ingest.extract(source_path)
        aliased, kinds = self.censors.censor_verified(text, allow_fragments=allow_fragments)
        spans = self.censors.last_spans
        fragment_hits = self.censors.last_fragments
        doc_id = self.next_id()

        # The original lives outside work/ and is never read by the agent.
        original_name = os.path.basename(source_path)
        if keep_original:
            shutil.copy2(source_path, os.path.join(self.private_dir, f"{doc_id}--{_slug(original_name)}{os.path.splitext(original_name)[1].lower()}"))

        body = (
            f"<!-- desensitized: {doc_id} · format {fmt} · generated {_now()} -->\n"
            f"# {doc_id}\n\n"
            f"Placeholders are `kind:TOKEN`. Treat them as opaque identifiers: quote them exactly,\n"
            f"never invent or expand one. The values they stand for are not available here.\n"
            + (f"\nREVIEW: {len(fragment_hits)} value(s) look damaged by extraction "
               f"({', '.join(sorted({s['kind'] for s in fragment_hits}))}) and were not tokenised. "
               f"Ask the curator, do not guess.\n" if fragment_hits else "")
            + f"\n---\n\n{aliased}\n"
        )
        work_path = os.path.join(self.work_dir, f"{doc_id}.md")
        with open(work_path, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(work_path, 0o644)

        entry = {
            "id": doc_id,
            "source_name": original_name,
            "format": fmt,
            "extractor": fmt,
            "aliased_path": f"{WORK}/{doc_id}.md",
            "private_path": f"{PRIVATE}/{doc_id}--{_slug(original_name)}{os.path.splitext(original_name)[1].lower()}" if keep_original else None,
            "bytes_in": os.path.getsize(source_path),
            "raw_chars": len(text),
            "chars_out": len(aliased),
            "kinds": kinds,
            "status": "needs_review" if fragment_hits else "aliased",
            # Span-level bookkeeping: how each value was found, never what it was.
            "spans": [{"kind": s["kind"], "method": s["method"], "start": s["start"],
                       "end": s["end"], "chars": s["end"] - s["start"],
                       "restorable": s["restorable"]} for s in spans],
            "fragments": [{"kind": s["kind"], "start": s["start"], "chars": s["end"] - s["start"]}
                          for s in fragment_hits],
            "aliased_sha256": hashlib.sha256(aliased.encode("utf-8")).hexdigest(),
            "added": _now(),
        }
        self.manifest["documents"].append(entry)
        self.censors.save()
        self._save_manifest()
        self.write_index()
        return entry

    def restore(self, text):
        return self.censors.restore(text)

    # -- alias -> value, at file level ------------------------------------
    @property
    def restored_dir(self):
        path = os.path.join(self.dir, RESTORED)
        os.makedirs(path, exist_ok=True)
        os.chmod(path, 0o700)
        return path

    def _doc_for_path(self, relative_path):
        """Map work/doc-003.md (or any file that names an id) back to its manifest entry."""
        base = os.path.basename(relative_path)
        for doc in self.manifest["documents"]:
            if base.startswith(doc["id"]):
                return doc
        return None

    def restore_file(self, source_path, fmt=None, out_dir=None):
        """Turn one aliased file back into values and write it to restored/.

        The whole-file counterpart of a snippet restore: every token must resolve, no
        token may survive, and the result is written 0600 into a 0700 directory. Refusals
        write nothing.
        """
        import emit as _emit
        if not os.path.isfile(source_path):
            raise WorkspaceFailure(f"no such file: {source_path}")
        with open(source_path, "r", encoding="utf-8") as fh:
            aliased = fh.read()
        aliased = _strip_generated_header(aliased)

        tokens = _censor.TOKEN_RE.findall(aliased)
        restored = self.restore(aliased)  # raises on any unknown token
        if _censor.TOKEN_RE.search(restored):
            raise WorkspaceFailure("tokens remain after restore; refusing to write a half-restored file")

        doc = self._doc_for_path(source_path)
        stem = os.path.splitext(os.path.basename(source_path))[0]
        name = f"{doc['id']}--{os.path.splitext(doc['source_name'])[0]}" if doc else stem
        fmt = (fmt or "md").lstrip(".").lower()
        target_dir = out_dir or self.restored_dir
        os.makedirs(target_dir, exist_ok=True)
        os.chmod(target_dir, 0o700)
        out_path = os.path.join(target_dir, f"{name}.restored.{fmt}")
        _emit.emit(restored, out_path, fmt)

        entry = {"source": os.path.relpath(source_path, self.dir), "restored": os.path.relpath(out_path, self.dir),
                 "doc": doc["id"] if doc else None, "tokens": len(tokens),
                 "kinds": sorted({kind for kind, _ in tokens}), "format": fmt, "at": _now(),
                 "restored_sha256": hashlib.sha256(restored.encode("utf-8")).hexdigest()}
        self._log_restore(entry)
        return entry

    def restore_all(self, fmt="md"):
        """Restore every .md file sitting in work/ — including anything the agent wrote."""
        out = []
        for name in sorted(os.listdir(self.work_dir)):
            if not name.endswith(".md") or name == INDEX:
                continue
            out.append(self.restore_file(os.path.join(self.work_dir, name), fmt=fmt))
        return out

    def _log_restore(self, entry):
        os.makedirs(self.state_dir, exist_ok=True)
        path = os.path.join(self.state_dir, "restores.jsonl")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    # -- index ------------------------------------------------------------
    def write_index(self):
        docs = self.manifest["documents"]
        totals = {}
        for doc in docs:
            for kind, count in doc["kinds"].items():
                totals[kind] = totals.get(kind, 0) + count
        lines = [
            f"# {self.project} — desensitised documents",
            "",
            f"{len(docs)} document(s). Placeholders are `kind:TOKEN`; the values live outside this",
            "directory. Quote a placeholder exactly; never invent or expand one.",
            "",
            "| id | source | format | placeholders | added |",
            "|---|---|---|---|---|",
        ]
        for doc in docs:
            kinds = ", ".join(f"{k}×{v}" for k, v in sorted(doc["kinds"].items())) or "none"
            lines.append(f"| `{doc['id']}` | {doc['source_name']} | {doc['format']} | {kinds} | {doc['added'][:16]} |")
        lines += ["", f"Totals: " + (", ".join(f"{k}×{v}" for k, v in sorted(totals.items())) or "no placeholders"),
                  "", "Files:", ""]
        lines += [f"- `{doc['aliased_path']}` — {doc['source_name']}" for doc in docs]
        with open(self.index_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    def status(self):
        return {
            "project": self.project,
            "workspace": self.dir,
            "work_dir": self.work_dir,
            "documents": [{k: d[k] for k in ("id", "source_name", "format", "kinds", "aliased_path")}
                          for d in self.manifest["documents"]],
        }


def soul_for(work_dir, project, model_label=None):
    """The worker's confinement, written next to the workspace it is allowed to touch."""
    model = model_label or "the configured model"
    return f"""# SOUL.md — desensitised document worker ({project})

## Where you are

You work in exactly one directory:

    {work_dir}

That directory is your entire world. Do not read, list, write or glob outside it — not
the parent, not `..`, not `~`, not `/tmp`. If a task seems to need a file that is not in
`{project}/work/`, stop and say so; do not go looking.

You will not find the real values behind the placeholders, because they are not in your
directory. That is deliberate. Do not try to recover, guess, infer or reconstruct them,
and do not ask anyone to send them to you.

## What you are given

Documents with every detected value replaced by `kind:TOKEN`, for example
`email:MFRGGZDFMZTWQ2LKNNWG23TPOB`. Each file starts with a short header and the project
index is `INDEX.md`.

A placeholder is an opaque identifier:

- quote it exactly, character for character;
- never invent one, never expand one, never split one across lines;
- never rename its kind;
- keep it where the original value was, in the same role.

`INDEX.md` lists which kinds appear in which document, and how many. It never contains a
value.

## What you do

Read the documents in `{project}/work/`, do the work asked of you, and write results back
into that same directory. If you produce a new file, name it so the id it belongs to is
obvious (`doc-003-summary.md`).

You run on {model}. You have no network and no shell outside your directory. Do not try
to install packages; if something is missing, report it.

## How you report

- Say which files you read and which you wrote.
- If a placeholder looked wrong (malformed, half-quoted, invented by an earlier step),
  say which file and which token — do not repair it silently.
- If the task cannot be done from the files present, say what is missing. Do not
  fabricate content to fill the gap.
- Never claim success you did not verify.
"""
