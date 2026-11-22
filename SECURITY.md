# Security

## What this protects

Text you send to a cloud model. Values matching a detector are replaced by
`kind:TOKEN` before the request leaves the host, and restored afterwards from a local
vault. The model never receives the value; it receives an opaque identifier it can
refer to.

## What it does not protect

* **Names without a roster.** Name detection needs a list. If your roster is
  incomplete, those names are sent as written.
* **Anything a detector does not match.** Look at the middle panel of the UI or
  `detect()` output before you trust a class of document.
* **The vault at rest.** `vault.json` is mode `0600`, not encrypted. Anyone who can
  read the file with the key can restore every value. Use disk encryption.
* **A hostile model.** If the model invents a token, the run is refused rather than
  guessed at, but nothing stops a model from writing about the content it saw.
* **Side channels.** Length, structure, sentence shape and timing are not censored.

## Reporting

Open an issue at
<https://github.com/samson1232/desensitizer-core-vagrant/issues> for defects. For
anything that could let a value leak past censoring, describe the input shape and the
kind that was missed — do not paste real personal data.

## Design rules that are not negotiable

1. Fail closed. If the post-censor scan is not empty, the run is refused.
2. A missing key is never re-minted; that would orphan every stored token.
3. An unknown token is never passed through as text.
4. The restore path imports only the standard library. It cannot reach a model.
5. The run log carries hashes and counts, never values.

## Credentials

The API key is read from the environment (or `--env-file` for local runs). It is never
written to the vault, the log, or the browser. A key with repo-wide or admin scopes is
not needed: this package only needs inference on one endpoint.
