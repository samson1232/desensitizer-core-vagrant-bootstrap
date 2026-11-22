# desensitizer-core-vagrant

A small, self-contained PII pipeline for text you are about to send to a **cloud** model:
censor locally, let the model work on opaque tokens, restore locally.

No local model. No rating, no routing, no lanes. No agents, no tool loops. Standard
library only — `git clone` is the install.

```
input ──► censor (local, keyed HMAC) ──► cloud model ──► restore (local, vault) ──► output
             │                                            │
             └── nothing but kind:TOKEN leaves             └── a dictionary lookup, not a model
```

## Why this exists

`desensitizer-core` is the full pipeline: local lanes, a frontier lane, a risk rating
that decides where work goes, an inspector UI, an agent harness. This is the cut-down
version for when you only ever use a cloud model and you want to read the whole thing
in one sitting:

| | desensitizer-core | this |
|---|---|---|
| models | local lane + frontier lane + fallback | one cloud endpoint, one model |
| routing | risk rating + threshold + lanes | none — everything goes to the cloud |
| decensor | in-process restore inside an agent run | **`decensor.py`, a standalone script** |
| agent | subagents, deployer, tool loop | none |
| deps | profile + engine + config | standard library |
| controls | 338 offline | 98 offline |

## Quick start

```sh
git clone https://github.com/samson1232/desensitizer-core-vagrant.git
cd desensitizer-core-vagrant
python3 verify.py                     # 98 offline controls, no key, no network
```

### Browser UI

```sh
# demo mode: no key, no network, answers locally
python3 ui_server.py

# live mode: one cloud model, exactly the one you name
python3 ui_server.py --live --api-key-env VAGRANT_API_KEY \
    --base-url https://api.deepseek.com --model deepseek-flash \
    --names "Alice Smith,Jane Roe"
```

Then open <http://127.0.0.1:8799/> (the server prints the port, and tries the next
one if it is taken). The page has three columns: what you typed, what would leave the
host, and what came back before/after restore. Buttons: **Censor only** (no network),
**Run · offline demo**, **Run · live cloud**, **Restore this panel**.

The key never reaches the browser: the page posts text to `/api/run`, the server
censors, calls the model, restores, and returns text.

### CLI, no server

```sh
# 1. censor a file
python3 - <<'EOF'
import censor
c = censor.Censor(".state/key.bin", ".state/vault.json", names=["Alice Smith"])
text = open("ticket.txt").read()
open("ticket.censored.txt", "w").write(c.censor_verified(text)[0])
c.save()
EOF

# 2. send ticket.censored.txt to whatever model you like

# 3. restore the reply — a script, no model, no network
python3 decensor.py --vault .state/vault.json --key .state/key.bin \
    --in reply.txt --out reply.restored.txt

# check first, write nothing:
python3 decensor.py --vault .state/vault.json --key .state/key.bin --check --in reply.txt
```

Exit code 2 means refused: an unknown token, the wrong key, or a token left behind.
Nothing is written on refusal — a half-restored document is worse than none.

## What "agentless" means here, and how it is enforced

`tests/test_decensor.py` parses the import graph of `decensor.py` and `censor.py` with
`ast` and asserts they import **only** the standard library (plus each other). If
anyone adds `urllib`, `http`, `socket`, `subprocess`, `openai` or `anthropic` to the
restore path, the suite fails. Restoring is a dictionary lookup and a string
substitution; it cannot call a model because it cannot reach one.

The same test file exercises the CLI end to end: byte-exact round trip, refusal on an
unknown token, refusal on the wrong key, `--check` writing nothing, `--stats`, stdin.

## How censoring works

Every value becomes `kind:TOKEN`, where the token is

```
base32( HMAC-SHA256(key, kind ‖ 0x00 ‖ value) )[:26]
```

* **keyed** — a bare hash of a phone number is a dictionary lookup away;
* **kind-scoped** — the same string in two roles gets two tokens;
* **fixed width** — token length never leaks value length;
* **fixed alphabet** — survives JSON, shell, URL and markdown contexts.

What is detected: `email`, `phone`, `staff_id`, `ni_number`, `postcode`, `ipv4`,
`mac`, `uuid`, `iban`, `dob`, `hostname`, `utr`, plus **names from a roster** you
supply (`--names`, `--names-file`, or `"names": []` in config). Names are not
guessable by regex, so they are not guessed: no roster, no name detection.

Fail-closed rules, all tested:

* censoring is verified — if anything still matches a detector afterwards, the run is
  refused rather than sent;
* restore refuses an unknown token (the model invented one, or the wrong vault is open);
* a vault written with a different key is an error, not a best effort;
* a missing key is never silently re-minted (that would orphan every stored token);
* a token that would collide with a different value is an error.

## The vault and the key

| file | mode | contains |
|---|---|---|
| `key.bin` | `0600` | 32 random bytes, created once, never leaves the host |
| `vault.json` | `0600` | `token -> {kind, value}` plus the key id |
| `runs.jsonl` | — | one line per run: hashes, counts, status. **No text, no values.** |

Lose the key and the tokens are unresolvable by design. Copy both to keep a history.

## Config

`config.example.json` (only needed for the library path; the UI takes flags):

```json
{
  "cloud": {
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "api_key_env": "VAGRANT_API_KEY",
    "timeout": 120,
    "max_tokens": 1024
  },
  "names": ["Alice Smith"]
}
```

`base_url` speaks the OpenAI chat-completions shape, so DeepSeek, OpenAI, Nous,
OpenRouter, Groq and others all work unchanged. There is no default endpoint and no
default model: you name them or it refuses to start.

## Tests

```sh
python3 verify.py          # 98 controls: censor, cloud, decensor, pipeline, ui
python3 verify.py --json   # machine-readable
```

| suite | what it controls |
|---|---|
| `test_censor.py` | every kind round-trips byte-exact; key/kind separation; collisions refuse; roster names; unicode; idempotence |
| `test_cloud.py` | request shape, auth header, timeouts, HTTP 429/500, non-JSON, empty content, missing key/model — plus the real serializer with `urlopen` stubbed |
| `test_decensor.py` | CLI round trip, refusals, `--check`, `--stats`, stdin, and the import-graph allowlist |
| `test_pipeline.py` | egress is scanned after censoring; invented tokens refuse; log holds no plaintext; vault persists |
| `test_ui.py` | routes, path-traversal guard, no-store headers, demo run, live-off refusal |

Every suite prints one `[PASS]`/`[FAIL]` line per control and `verify.py` enforces a
per-suite floor, so a suite that stops asserting is a red run rather than a quiet one.

## Vagrant

```sh
vagrant up      # ubuntu/noble, installs python3, runs verify.py in provisioning
vagrant ssh
```

## Deliberately missing

* **No speculator on quality.** This censors what it can prove and refuses the rest; it
  does not score outputs.
* **No name detection without a roster.** Add names, or accept that names pass through.
* **No retries.** A failed cloud call is a refusal. If you want retry/backoff, put it in
  your caller where you can see it.
* **No encryption at rest.** `vault.json` is `0600`, not encrypted. Use disk encryption.

## Licence

MIT — see `LICENSE`.
