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
    # Windows/Dell-style device names, labelled or bare: DESKTOP-9941A, LAPTOP-7C2K9Q.
    # Restricted to the known prefixes so ordinary CAPS-WORDS do not get caught.
    ("device", re.compile(r"\b(?:DESKTOP|LAPTOP|WORKSTATION|SERVER|WS)-[A-Z0-9]{4,12}\b")),
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
    "device": "DESKTOP-9941A",
    "utr": "1234567890",
}

# Extraction is lossy. pdftotext and OCR insert spaces inside values, swap O/0 and l/1,
# split a value across a line break, and clip the tail of a line at the page edge. These
# patterns catch the *shape* of that damage for the kinds where a near-miss is still a
# disclosure. A loose match is tokenised like any other, so restore stays byte-exact --
# it restores the damaged text, which is what the file actually contains.
LOOSE_PATTERNS = [
    ("email", re.compile(r"[A-Za-z0-9._%+\-]{1,64}\s?@\s?[A-Za-z0-9.\-]{1,64}\s?\.\s?[A-Za-z]{2,6}")),
    ("mac", re.compile(r"(?<![A-Za-z0-9])(?:[0-9A-Fa-f]{2}[:-][ \t]?){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])")),
    ("device", re.compile(r"\b(?:DESKTOP|LAPTOP|WORKSTATION|SERVER|WS)[\s\-\u2013\u2014]{0,3}[A-Z0-9]{3,12}\b")),
    ("phone", re.compile(r"(?<![\w+])\+\d{1,3}[ \-.]?\(?\d{1,4}\)?[ \-.]?\d{3,4}[ \-.]?\d{3,4}(?!\w)")),
    ("ipv4", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)[. ]){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b")),
]

# Damage that cannot be tokenised because the value itself is incomplete. These are not
# secrets you can restore -- they are evidence the extractor ate something, so the
# document is flagged `needs_review` instead of being shipped quietly.
FRAGMENT_PATTERNS = [
    ("mac", re.compile(r"\b(?:[0-9A-Fa-f]{2}:){4,5}[0-9A-Fa-f]{1,2}\b(?!:)")),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){2,3}$")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+$")),
    ("device", re.compile(r"\b(?:DESKTOP|LAPTOP|WORKSTATION|SERVER|WS)-[A-Z0-9]{1,2}\b")),
]


def _mac(key, kind, value):
    return hmac.new(key, f"{kind}\x00{value}".encode("utf-8"), hashlib.sha256).digest()


def make_token(key, kind, value):
    """Fixed-width, kind-scoped, key-salted token. Pure function."""
    raw = base64.b32encode(_mac(key, kind, value)).decode("ascii").rstrip("=")
    return raw[:TOKEN_CHARS]


def _mask_tokens(text):
    """Replace existing tokens with same-length sentinels so they are not re-detected."""
    return TOKEN_RE.sub(lambda m: "\x00" * len(m.group(0)), text)


def _collect(text, extra=()):
    """All candidate spans, tagged with how they were found. Overlaps resolved once."""
    masked = _mask_tokens(text)
    families = [(0, "strict", tuple(extra) + tuple(PATTERNS)),
                (1, "loose", tuple(LOOSE_PATTERNS))]
    found = []
    for base, method, patterns in families:
        for order, (kind, rx) in enumerate(patterns):
            for m in rx.finditer(masked):
                if m.end() > m.start():
                    found.append({"start": m.start(), "end": m.end(), "kind": kind,
                                  "value": text[m.start():m.end()], "method": method,
                                  "rank": base * 100 + order, "restorable": True})
    found.sort(key=lambda s: (s["start"], -(s["end"] - s["start"]), s["rank"]))
    picked, last_end = [], -1
    for span in found:
        if span["start"] >= last_end:
            picked.append(span)
            last_end = span["end"]

    # Fragments: damage with no complete value to tokenise. Only look where nothing was
    # already claimed, so a matched MAC is not also reported as a fragment of itself.
    claimed = [(s["start"], s["end"]) for s in picked]
    for kind, rx in FRAGMENT_PATTERNS:
        for m in rx.finditer(masked):
            if m.end() <= m.start():
                continue
            if any(m.start() < end and m.end() > start for start, end in claimed):
                continue
            picked.append({"start": m.start(), "end": m.end(), "kind": kind,
                           "value": text[m.start():m.end()], "method": "fragment",
                           "rank": 900, "restorable": False})
    picked.sort(key=lambda s: s["start"])
    return picked


def detect(text, extra=()):
    """Return [(start, end, kind, value)] for restorable spans, non-overlapping.

    Overlaps resolve by earliest start, then longest match, then pattern family
    (strict, then loose, then roster).
    """
    return [(s["start"], s["end"], s["kind"], s["value"])
            for s in _collect(text, extra) if s["restorable"]]


def detect_spans(text, extra=()):
    """The same scan, with the bookkeeping the database stores: method and restorability.

    Everything the schema needs to represent damaged text honestly: strict matches,
    loose matches (spaces or swapped separators inside a value), and fragments (a value
    the extractor clipped, which cannot be restored and must be reviewed).
    """
    return _collect(text, extra)


def fragments(text, extra=()):
    """Only the non-restorable hits: evidence that extraction damaged a value."""
    return [s for s in _collect(text, extra) if not s["restorable"]]


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
        self.last_spans = []
        self.last_fragments = []
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
        spans = detect_spans(text, self.roster)
        self.last_spans = spans
        self.last_fragments = [s for s in spans if not s["restorable"]]
        out, cursor, counts = [], 0, {}
        for span in spans:
            if not span["restorable"]:
                continue
            kind, value = span["kind"], span["value"]
            token = f"{kind}:{make_token(self.key, kind, value)}"
            known = self.tokens.get(token)
            if known and known["value"] != value:
                raise CensorFailure(f"token collision on {token}: {known['value']!r} vs {value!r}")
            self.tokens.setdefault(token, {"kind": kind, "value": value})
            out.append(text[cursor:span["start"]])
            out.append(token)
            cursor = span["end"]
            counts[kind] = counts.get(kind, 0) + 1
        out.append(text[cursor:])
        return "".join(out), counts

    def censor_verified(self, text, allow_fragments=False):
        """Censor, then prove the result contains no detectable value. Fail closed.

        Fragments are evidence that extraction damaged a value (a MAC clipped at the page
        edge, an address missing its domain). They cannot be tokenised because there is
        no complete value, so the default is to refuse the document rather than ship a
        partial identifier. ``allow_fragments=True`` ships it marked needs_review.
        """
        censored, counts = self.censor(text)
        leaks = detect(censored, self.roster)
        if leaks:
            raise CensorFailure("censored text still matches detectors: " + ", ".join(sorted({k for _, _, k, _ in leaks})))
        if self.last_fragments and not allow_fragments:
            detail = ", ".join(f"{s['kind']}@{s['start']}({len(s['value'])}ch)" for s in self.last_fragments[:5])
            raise CensorFailure(
                f"extraction damage in {len(self.last_fragments)} place(s): {detail} — "
                "a value looks clipped or malformed; re-extract the source, or alias it with "
                "allow_fragments=True to ship it flagged needs_review")
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
