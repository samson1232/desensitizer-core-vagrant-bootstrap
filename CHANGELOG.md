# Changelog

All notable changes to this project. Format follows Keep a Changelog; versions use
semantic versioning.

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
