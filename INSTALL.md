# Install: from a fresh Hermes install to two working agents

This is the whole path, in order. It assumes Hermes is installed (`hermes --version`
answers) and that you can run `python3` and `uv`.

Total time: a few minutes. Nothing here downloads a model.

---

## 0. What you are building

```
  you ──upload──►  Flask + SQLite integration layer  ──alias──►  work/  (kind:TOKEN only)
                          │                                        │
                          │ jobs table                             │ the worker agent's cwd
                          ▼                                        ▼
                   curator profile  ◄──── hermes -p desens-<p> ────►  worker profile
                   (ingest/restore)                                (confined to work/)
                          │
                          └──restore──►  restored/  (values back, 0600)
```

Two Hermes profiles, one database, one directory the agent may touch.

| piece | owns |
|---|---|
| `desens-<project>` (worker) | reads `work/`, writes results there. cwd pinned. SOUL confines it. |
| `desens-<project>-curator` | owns the project directory: ingest, index, restore. Not the analysis. |
| `corpus.db` | documents, jobs, restores, events — never a value |
| `vault.json` + `key.bin` | the only place a token's value exists (0600) |

---

## 1. Get the package

```sh
git clone https://github.com/samson1232/desensitizer-core-vagrant-bootstrap.git
cd desensitizer-core-vagrant-bootstrap
```

Optional but recommended for PDFs:

```sh
# Debian/Ubuntu
sudo apt install poppler-utils        # provides pdftotext
# or: uv pip install pymupdf
```

## 2. Prove it runs before you configure anything

```sh
python3 verify.py            # 166 offline controls, no key, no network
```

The Flask suite needs Flask; install the extras first if you want it to run here:

```sh
uv venv
uv pip install -r requirements.txt
python3 verify.py --strict   # now nothing may be skipped
```

## 3. Build the two agents and the corpus

```sh
python3 bootstrap.py --project acme --root ~/desensitizer \
    --names "Alice Smith,Bob Jones"
```

That single command:

- creates `~/desensitizer/acme/{work,private,state,restored}` with `private/` and
  `state/` at mode `0700`;
- creates the two profiles with `hermes profile create`, each cloned from your active
  profile so the model and credentials already work;
- writes `SOUL.md` into both profile directories;
- pins `terminal.cwd` to `acme/work` for the worker and to `acme/` for the curator;
- disables the web/browser/image/tts toolsets for both;
- prints the two commands you will actually use.

Check it decided what you think it decided:

```sh
hermes -p desens-acme config get terminal.cwd      # → ~/desensitizer/acme/work
python3 bootstrap.py --project acme --root ~/desensitizer --verify
```

Add `--dry-run` at any point to see the plan without touching anything.

## 4. Start the integration layer

```sh
.venv/bin/python app.py --project acme --root ~/desensitizer            # demo mode
# or, against a real cloud model:
.venv/bin/python app.py --project acme --root ~/desensitizer --live \
    --env-file ~/.hermes/.env --api-key-env DEEPSEEK_API_KEY \
    --base-url https://api.deepseek.com --model deepseek-chat
```

Open <http://127.0.0.1:8820/>. Upload a `.pdf`, `.docx`, `.xlsx` or plain text file; it is
extracted, aliased, and written to `acme/work/doc-00N.md`. The page lists what is in the
corpus and the placeholders each document contains.

The same thing from the shell:

```sh
python3 ingest_cli.py --project acme --root ~/desensitizer add ticket.pdf notes.docx \\
    --names "Alice Smith"
python3 ingest_cli.py --project acme --root ~/desensitizer status
```

## 5. Let the agent work

From the UI (**jobs** panel) or directly:

```sh
# the worker: cwd is already work/, and SOUL tells it not to leave
hermes -p desens-acme chat
```

A job sent through the UI runs the same thing non-interactively, records the exit code,
and stores the output in `corpus.db`:

```sh
curl -s -XPOST localhost:8820/api/jobs \
  -H 'content-type: application/json' \
  -d '{"prompt":"Summarise doc-001 for the customer","doc_ids":["doc-001"]}'
curl -s -XPOST localhost:8820/api/jobs/<id>/run
curl -s localhost:8820/api/jobs/<id>
```

The agent sees only `kind:TOKEN`. Its output stays in `work/`.

## 6. Alias → value: get the real file back

When the agent's output is right, restore it. Every token must resolve or nothing is
written:

```sh
python3 ingest_cli.py --project acme --root ~/desensitizer restore-doc \\
    --doc doc-001 --fmt docx          # → acme/restored/doc-001--ticket.restored.docx
python3 ingest_cli.py --project acme --root ~/desensitizer restore-all --fmt md
```

Or from the UI: the **restore** button per document, which writes the same file and offers
it as a download.

Formats: `md`, `txt`, `docx`, `xlsx`. PDF output is refused on purpose — a document that
was flattened to text cannot be un-flattened, and a fake PDF would be worse than a `.md`.

## 7. What the confinement is, and is not

It is three things stacked:

1. `terminal.cwd` = `acme/work`, so relative paths land there;
2. `SOUL.md` tells the worker that directory is its whole world, and the tests assert the
   text says so;
3. the real values are not in `acme/work` at all — `private/` (originals) and `state/`
   (vault, key) are outside it.

It is **not** an OS sandbox. The worker runs as your user, so a determined agent could read
`../private` or `../state`. If you need that boundary to be real, run the worker profile as
a different Unix user or in a container with only `acme/work` mounted — see `SECURITY.md`.

## 8. Troubleshooting

| symptom | cause | fix |
|---|---|---|
| `pdf has no text layer` | scanned PDF | OCR it first; the package refuses to ship an empty page |
| `pdf support needs poppler` | no `pdftotext`, no PyMuPDF | `apt install poppler-utils` |
| `vault ... was written with key` | key file replaced | copy `state/key.bin` back; tokens are not recoverable without it |
| `restore refused: no vault entry` | the model invented a token, or you restored against another project | check the token against `work/INDEX.md` |
| `profile create failed` | Hermes not on PATH for the bootstrap shell | `--hermes-bin /path/to/hermes` |
| `SKIP test_app.py` | no Flask | `uv venv && uv pip install -r requirements.txt` |
