"""The whole pipeline: censor -> one cloud call -> restore -> prove the round trip.

There is no router and no second path. A prompt either goes out with every
detected value replaced by a keyed token, comes back, and is restored locally — or
the run is refused and nothing is returned to the caller.

The guarantee this module is built to keep: **the outbound payload contains no
value the detectors can find**, and **every token in the inbound text resolves in
this installation's vault**. Both are asserted, not assumed, on every run.
"""
import datetime
import hashlib
import json
import os
import time
import uuid

import censor as _censor

DEFAULT_SYSTEM = (
    "You are a writing assistant working on documents that have been redacted for privacy. "
    "Placeholders look like kind:TOKEN (for example email:MFRGGZDFMZTWQ2LKNNWG23TPOB). Treat a "
    "placeholder as an opaque identifier: quote it exactly, never invent one, never expand one, "
    "and never guess what it stands for."
)


class RunRefused(Exception):
    """The run failed a gate. Nothing is returned to the caller."""

    def __init__(self, stage, reason):
        super().__init__(f"{stage}: {reason}")
        self.stage = stage
        self.reason = reason


class RunResult:
    __slots__ = ("run_id", "ok", "stage", "reason", "censored", "raw", "restored",
                 "kinds", "model", "elapsed_ms")

    def __init__(self, **kw):
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot))

    def as_dict(self):
        return {slot: getattr(self, slot) for slot in self.__slots__}

    def __repr__(self):
        return f"<RunResult {self.run_id} ok={self.ok} stage={self.stage} kinds={self.kinds}>"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _log(path, record):
    """Append one JSON line. Only hashes and counts go in the log, never text."""
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def run(prompt, censors, client, system=DEFAULT_SYSTEM, log_path=None):
    """Run one prompt through the pipeline.

    ``censors`` is a ``censor.Censor``. ``client`` is a ``cloud.CloudClient`` or
    anything with the same ``complete()``. Raises ``RunRefused`` on any gate; the
    caller gets no partial answer that could be mistaken for a clean one.
    """
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    started = time.time()
    record = {
        "run_id": run_id,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "model": getattr(client, "model", None),
    }

    # Gate 1 — the outbound payload must be clean, and proven clean after the fact.
    try:
        censored, kinds = censors.censor_verified(prompt)
    except _censor.CensorFailure as exc:
        _log(log_path, {**record, "status": "refused", "stage": "censor", "reason": str(exc)})
        raise RunRefused("censor", str(exc)) from None
    if _censor.detect(censored):
        _log(log_path, {**record, "status": "refused", "stage": "censor", "reason": "post-censor scan not empty"})
        raise RunRefused("censor", "post-censor scan not empty")

    record.update({"kinds": kinds, "outbound_sha256": _sha(censored), "outbound_chars": len(censored)})

    # Gate 2 — one cloud call. A transport failure is a refusal, not a retry loop.
    try:
        raw = client.complete(censored, system=system)
    except Exception as exc:  # CloudError, or anything the transport raised
        _log(log_path, {**record, "status": "refused", "stage": "cloud", "reason": str(exc)})
        raise RunRefused("cloud", str(exc)) from None

    record.update({"inbound_sha256": _sha(raw), "inbound_chars": len(raw)})

    # Gate 3 — every token that came back must resolve here. An unknown token means
    # the model invented one (or the wrong vault is loaded): refuse rather than
    # hand back text that still looks censored.
    try:
        restored = censors.restore(raw)
    except _censor.CensorFailure as exc:
        _log(log_path, {**record, "status": "refused", "stage": "restore", "reason": str(exc)})
        raise RunRefused("restore", str(exc)) from None
    if _censor.TOKEN_RE.search(restored):
        _log(log_path, {**record, "status": "refused", "stage": "restore", "reason": "tokens remain after restore"})
        raise RunRefused("restore", "tokens remain after restore")

    censors.save()
    elapsed = int((time.time() - started) * 1000)
    _log(log_path, {**record, "status": "ok", "elapsed_ms": elapsed, "restored_sha256": _sha(restored)})
    return RunResult(run_id=run_id, ok=True, stage="done", reason=None, censored=censored,
                     raw=raw, restored=restored, kinds=kinds,
                     model=getattr(client, "model", None), elapsed_ms=elapsed)
