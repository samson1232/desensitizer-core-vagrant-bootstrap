"""Censoring, keyed tokens, and the vault — deterministic, model-free, agent-free.

Every value that must not leave the host is replaced by ``kind:TOKEN``:

    alice.smith@example.com  ->  email:MFRGGZDFMZTWQ2LKNNWG23TPOB

The token is ``base32(HMAC-SHA256(key, kind \\x00 value))[:26]``. Properties that
matter and are tested in ``tests/test_censor.py``:

* the key is in the derivation, so the token cannot be recovered by hashing a
  guessed value (a bare sha256 of a phone number is a dictionary lookup away);
* the kind is in the derivation, so the same string in two roles gets two tokens;
* the width is fixed, so token length never leaks value length;
* the alphabet is fixed, so the token survives JSON, shell and URL contexts.

This module never calls a model, opens a socket, or spawns a process. It imports
only the standard library, which ``tests/test_decensor.py`` enforces by parsing
the import graph.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets

TOKEN_CHARS = 26
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"  # RFC 4648 base32, no padding
KEY_BYTES = 32
VAULT_VERSION = 1

# A token as it appears on the wire: a lowercase kind, a colon, 26 base32 chars.
TOKEN_RE = re.compile(r"(?<![\w:])([a-z][a-z0-9_]{1,31}):([A-Z2-7]{26})(?![\w:])")


class CensorFailure(Exception):
    """The pass could not prove the text safe. Always fails closed."""


class VaultMismatch(CensorFailure):
    """The vault was written with a different key than the one loaded."""


# --------------------------------------------------------------------- detection
# Deliberately conservative. Each pattern must be unambiguous on its own: a
# pattern that guesses costs more (censoring prose, breaking filenames) than it
# saves. Add kinds here, add a canary in CANARIES below, add a control in the
# tests — the suite fails if a kind has no canary.
PATTERNS = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"(?<![\w+])\+\d{1,3}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,4}(?!\w)")),
    ("staff_id", re.compile(r"\bSTF-\d{4}\b")),
    ("ni_number", re.compile(r"\b[A-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b")),
    ("postcode", re.compile(r"\b[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2}\b")),
    ("ipv4", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b")),
    ("mac", re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")),
    ("uuid", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("iban", re.compile(r"\bGB\d{2}[A-Z]{4}\d{14}\b")),
    ("dob", re.compile(r"\b(?:19[3-9]\d|200\d)-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b")),
    ("hostname", re.compile(r"\b(?:ops|eng|hr|it)-ws-\d{3}\b")),
    ("utr", re.compile(r"\b\d{10}\b")),
]

# One planted value per kind. self_test() walks this, so a kind without a canary
# is a failure rather than a silent gap in coverage.
CANARIES = {
    "email": "alice.smith@example.com",
    "phone": "+447700900123",
    "staff_id": "STF-4821",
    "ni_number": "QQ 12 34 56 C",
    "postcode": "SW1A 1AA",
    "ipv4": "192.0.2.44",
    "mac": "00:1B:44:11:3A:B7",
    "uuid": "123e4567-e89b-12d3-a456-426614174000",
    "iban": "GB33BUKB20201555555555",
    "dob": "1984-07-19",
    "hostname": "ops-ws-014",
    "utr": "1234567890",
}


def _mac(key, kind, value):
    return hmac.new(key, f"{kind}\x00{value}".encode("utf-8"), hashlib.sha256).digest()


def make_token(key, kind, value):
    """Fixed-width, kind-scoped, key-salted token. Pure function."""
    raw = base64.b32encode(_mac(key, kind, value)).decode("ascii").rstrip("=")
    return raw[:TOKEN_CHARS]


def _mask_tokens(text):
    """Replace existing tokens with same-length sentinels so they are not re-detected."""
    return TOKEN_RE.sub(lambda m: "\x00" * len(m.group(0)), text)


def detect(text, extra=()):
    """Return [(start, end, kind, value)] for the original text, non-overlapping.

    ``extra`` is additional (kind, compiled) pairs — the roster-based name pattern,
    which only exists when a roster is supplied. Overlaps resolve by earliest start,
    then longest match, then pattern order (roster first, so a known name wins over
    a shorter generic rule that starts at the same place).
    """
    masked = _mask_tokens(text)
    found = []
    for order, (kind, rx) in enumerate(tuple(extra) + tuple(PATTERNS)):
        for m in rx.finditer(masked):
            found.append((m.start(), m.end(), order, kind, text[m.start():m.end()]))
    found.sort(key=lambda c: (c[0], -(c[1] - c[0]), c[2]))
    picked, last_end = [], -1
    for start, end, _order, kind, value in found:
        if start >= last_end:
            picked.append((start, end, kind, value))
            last_end = end
    return picked


def compile_roster(names):
    """Names are not guessable by regex, so they come from a roster: one alternation,
    longest first so 'Alice Smith-Jones' wins over 'Alice Smith' at the same start."""
    names = [n.strip() for n in (names or []) if n and n.strip()]
    if not names:
        return []
    names.sort(key=len, reverse=True)
    body = "|".join(re.escape(n) for n in names)
    return [("name", re.compile(rf"\b(?:{body})\b"))]


# ------------------------------------------------------------------------- keys
def load_or_create_key(path, create=True):
    """32 key bytes from ``path``, mode 0600. Never silently replaced."""
    if os.path.exists(path):
        with open(path, "rb") as fh:
            data = fh.read()
        if len(data) != KEY_BYTES:
            raise CensorFailure(f"key file {path} is {len(data)} bytes, expected {KEY_BYTES}")
        return data
    if not create:
        raise CensorFailure(f"key file {path} is missing; refusing to mint a new one (every stored token would stop resolving)")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    key = secrets.token_bytes(KEY_BYTES)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


def key_id(key):
    return hashlib.sha256(b"vagrant-key-id\x00" + key).hexdigest()[:16]


def load_config(path, required=False):
    """Config is optional: with no file, censor-only use still works."""
    if not path or not os.path.exists(path):
        if required:
            raise CensorFailure(f"config file {path} is missing")
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class Censor:
    """Censor + restore against a key file and a JSON vault.

    The vault is the only place a token's meaning exists. It is written 0600,
    atomically, and stamped with a key id: opening a vault with the wrong key is
    an error, not a best effort.
    """

    def __init__(self, key_path, vault_path, create_key=True, names=None):
        self.key_path = key_path
        self.vault_path = vault_path
        self.key = load_or_create_key(key_path, create=create_key)
        self.names = [n for n in (names or []) if n]
        self.roster = compile_roster(self.names)
        self.tokens = {}
        self._load_vault()

    # -- vault ------------------------------------------------------------
    def _load_vault(self):
        if not os.path.exists(self.vault_path):
            return
        with open(self.vault_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("version") != VAULT_VERSION:
            raise CensorFailure(f"vault {self.vault_path} has version {data.get('version')}, expected {VAULT_VERSION}")
        if data.get("key_id") != key_id(self.key):
            raise VaultMismatch(f"vault {self.vault_path} was written with key {data.get('key_id')}; loaded key is {key_id(self.key)}")
        self.tokens = data.get("tokens", {})

    def save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.vault_path)) or ".", exist_ok=True)
        payload = {"version": VAULT_VERSION, "key_id": key_id(self.key), "tokens": self.tokens}
        tmp = f"{self.vault_path}.tmp{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, self.vault_path)

    # -- censor -----------------------------------------------------------
    def censor(self, text):
        """Return (censored_text, {kind: count}). Idempotent for already-tokenised text."""
        spans = detect(text, self.roster)
        out, cursor, counts = [], 0, {}
        for start, end, kind, value in spans:
            token = f"{kind}:{make_token(self.key, kind, value)}"
            known = self.tokens.get(token)
            if known and known["value"] != value:
                raise CensorFailure(f"token collision on {token}: {known['value']!r} vs {value!r}")
            self.tokens.setdefault(token, {"kind": kind, "value": value})
            out.append(text[cursor:start])
            out.append(token)
            cursor = end
            counts[kind] = counts.get(kind, 0) + 1
        out.append(text[cursor:])
        return "".join(out), counts

    def censor_verified(self, text):
        """Censor, then prove the result contains no detectable value. Fail closed."""
        censored, counts = self.censor(text)
        leaks = detect(censored, self.roster)
        if leaks:
            raise CensorFailure("censored text still matches detectors: " + ", ".join(sorted({k for _, _, k, _ in leaks})))
        return censored, counts

    # -- restore ----------------------------------------------------------
    def restore(self, text):
        """Replace every token with its value. An unknown token is a hard error."""
        missing = []

        def repl(m):
            token = m.group(0)
            entry = self.tokens.get(token)
            if entry is None:
                missing.append(token)
                return token
            return entry["value"]

        out = TOKEN_RE.sub(repl, text)
        if missing:
            raise CensorFailure("no vault entry for " + ", ".join(sorted(set(missing))[:4]))
        return out

    def self_test(self):
        """Canaries: every kind must survive censor -> restore byte-exact."""
        problems = []
        for kind, value in CANARIES.items():
            text = f"before {value} after"
            censored, counts = self.censor_verified(text)
            if counts.get(kind) != 1:
                problems.append(f"{kind}: expected 1 censored value, got {counts}")
                continue
            if value in censored:
                problems.append(f"{kind}: plaintext survived censoring")
            if self.restore(censored) != text:
                problems.append(f"{kind}: round trip is not byte-exact")
        uncovered = set(CANARIES) - {k for k, _ in PATTERNS}
        unknown = {k for k, _ in PATTERNS} - set(CANARIES)
        if uncovered or unknown:
            problems.append(f"canary/pattern mismatch: {sorted(uncovered | unknown)}")
        for name in self.names:
            text = f"ticket for {name} please"
            try:
                censored, counts = self.censor_verified(text)
            except CensorFailure as exc:
                problems.append(f"name/{name}: {exc}")
                continue
            if counts.get("name") != 1 or name in censored:
                problems.append(f"name/{name}: not censored ({counts}, {censored!r})")
            elif self.restore(censored) != text:
                problems.append(f"name/{name}: round trip is not byte-exact")
        return problems
