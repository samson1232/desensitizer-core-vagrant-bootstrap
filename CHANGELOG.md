# Changelog

All notable changes to this project. Format follows Keep a Changelog; versions use
semantic versioning.

## [0.2.0] — 2026-11-22

The bootstrap distribution: documents in, an agent confined to aliased copies, and a
restore path that puts the values back.

### Added

- `ingest.py` — text extraction for `.pdf` (pdftotext or PyMuPDF), `.docx` and
  `.xlsx/.xlsm` (zipfile + XML, no third-party readers), and plain text. A scanned PDF is
  a refusal with instructions, never an empty document.
- `workspace.py` — the corpus layout (`work/` for the agent, `private/` for originals,
  `state/` for key + vault + manifest, `restored/` for outputs), roster-name aliasing on
  top of the 12 regex kinds, `INDEX.md` per project.
- **Alias -> value workflow at file level**: `restore_file()` / `restore_all()` and
  `emit.py` write restored text back out as `md`, `txt`, `docx` or `xlsx` (PDF refused by
  design). Unknown tokens refuse; nothing is written on refusal.
- `ingest_cli.py` — `add`, `index`, `status`, `restore`, `restore-doc`, `restore-all`.
- `bootstrap.py` — builds the two-agent setup from a fresh Hermes install: profiles
  `desens-<project>` (worker, cwd pinned to `work/`) and `desens-<project>-curator`, each
  with a generated `SOUL.md`, `terminal.cwd`, and disabled web/browser/image/tts
  toolsets. `--dry-run`, `--verify`, idempotent re-runs.
- **Flask + SQLite integration layer** (`app.py`, `db.py`): documents, jobs, restores and
  events in `state/corpus.db`; upload, restore, job dispatch to `hermes -p ... -q`, and
  download of restored files. The database holds ids, paths, kinds and hashes — never a
  value, asserted against the on-disk bytes.
- Frontend corpus panel: upload, live document list, `INDEX.md` view, copy-work-dir.
- `INSTALL.md`, `requirements.txt` (Flask only), `--strict` in `verify.py`.
- 166 offline controls across nine suites.

## [0.1.0] — 2026-11-22

First cut. A deliberately small derivative of `desensitizer-core`: cloud models only,
no rating and no routing, and a decensor step that is a script rather than an agent.

### Added

- `censor.py` — 12 regex detectors plus a roster-based name detector; keyed
  `kind:BASE32` tokens (HMAC-SHA256, 26 chars); `0600` key and vault; fail-closed
  restore, collision detection and wrong-key detection.
- `cloud.py` — one OpenAI-shaped chat-completions call over `urllib`, injectable
  transport, no SDK, no default endpoint or model.
- `pipeline.py` — censor → one call → restore, with three gates (clean egress, cloud
  result, every token resolves) and a JSONL run log that carries hashes, not text.
- `decensor.py` — standalone agentless restore: byte-exact, refuses unknown tokens and
  the wrong key, writes nothing on refusal. Import graph restricted to the standard
  library and `censor` by test.
- `ui_server.py` + `ui/` — loopback-only browser front end: censor, offline demo run,
  live cloud run, restore. API key stays server-side.
- `verify.py` + `tests/` — 98 offline controls across five suites, each with a
  minimum-check floor.
- `Vagrantfile` — `vagrant up` provisions python3 and runs the suite.

### Notes

- Names are only censored when a roster is supplied. There is no name guesser, and no
  attempt to infer one.
- No retries: a failed cloud call is a refusal.
