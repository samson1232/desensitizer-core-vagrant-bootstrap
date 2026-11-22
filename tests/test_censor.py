"""Controls for the censor pass. Every kind has a canary; the rounding is byte-exact."""
import os
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import censor  # noqa: E402

FAILS = []


def chk(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)
    return ok


def main():
    tmp = tempfile.mkdtemp(prefix="censortest")
    try:
        key = os.path.join(tmp, "key.bin")
        vault = os.path.join(tmp, "vault.json")
        c = censor.Censor(key, vault)

        for kind, value in censor.CANARIES.items():
            text = f"before {value} after"
            censored, counts = c.censor_verified(text)
            ok = (counts.get(kind) == 1 and value not in censored
                  and c.restore(censored) == text)
            chk(f"round-trip/{kind}", ok, f"counts={counts} censored={censored!r}")

        tok = censor.make_token(c.key, "email", "a@b.co")
        chk("token/width", len(tok) == censor.TOKEN_CHARS, f"len={len(tok)}")
        chk("token/alphabet", all(ch in censor.ALPHABET for ch in tok), tok)
        chk("token/stable", censor.make_token(c.key, "email", "a@b.co") == tok)
        chk("token/domain-separated",
            censor.make_token(c.key, "email", "1234567890") != censor.make_token(c.key, "utr", "1234567890"))
        other = censor.load_or_create_key(os.path.join(tmp, "other.bin"))
        chk("token/key-separated", censor.make_token(other, "email", "a@b.co") != tok)

        censored, _ = c.censor("mail alice.smith@example.com now")
        again, counts2 = c.censor(censored)
        chk("censor/idempotent", again == censored and counts2 == {}, f"{again!r} {counts2}")

        try:
            c.restore("email:AAAAAAAAAAAAAAAAAAAAAAAAAA")
            chk("restore/unknown-token-refused", False, "no exception")
        except censor.CensorFailure:
            chk("restore/unknown-token-refused", True)

        c.save()
        c2 = censor.Censor(key, vault)
        chk("vault/persisted", c2.restore(censored) == "mail alice.smith@example.com now")

        try:
            censor.Censor(os.path.join(tmp, "other.bin"), vault)
            chk("vault/wrong-key-refused", False, "no exception")
        except censor.VaultMismatch:
            chk("vault/wrong-key-refused", True)

        mode = os.stat(key).st_mode & 0o777
        chk("key/mode-0600", mode == 0o600, oct(mode))

        try:
            censor.Censor(os.path.join(tmp, "absent.bin"), os.path.join(tmp, "absent.json"), create_key=False)
            chk("key/missing-refused", False, "no exception")
        except censor.CensorFailure:
            chk("key/missing-refused", True)

        victim = "someone@example.com"
        tok2 = f"email:{censor.make_token(c.key, 'email', victim)}"
        c.tokens[tok2] = {"kind": "email", "value": "different@example.com"}
        try:
            c.censor(f"mail {victim}")
            chk("censor/collision-refused", False, "no exception")
        except censor.CensorFailure:
            chk("censor/collision-refused", True)
        del c.tokens[tok2]

        # Longest match at the same start wins: the digits inside an email address
        # must not be tokenised separately from the address that contains them.
        mixed, counts3 = c.censor("write to 1234567890@example.com")
        chk("detect/overlap-longest-wins",
            counts3.get("email") == 1 and "utr" not in counts3, f"{counts3}")

        prose, counts4 = c.censor("create answer.py and mail alice.smith@example.com")
        chk("detect/filename-preserved",
            "answer.py" in prose and "alice.smith@example.com" not in prose and counts4.get("email") == 1, prose)

        untouched, counts5 = c.censor("shipped 1899-01-01 and 2026-13-01")
        chk("detect/out-of-range-dates-preserved", counts5 == {}, f"{counts5} {untouched!r}")

        uni = "naïve — 日本語 alice.smith@example.com ✓"
        cen, _ = c.censor(uni)
        chk("censor/unicode-preserved", c.restore(cen) == uni and "日本語" in cen)

        # Names come from a roster, not a guess: the alternation must prefer the
        # longer name at the same start and leave near-misses alone.
        roster = censor.Censor(key, os.path.join(tmp, "roster.json"),
                               names=["Alice Smith", "Alice Smith-Jones"])
        cen2, counts6 = roster.censor_verified("for Alice Smith-Jones and Alice Smith, not Alicia Smithson")
        chk("roster/censors-known-names", counts6.get("name") == 2, counts6)
        chk("roster/longest-name-wins", "Alice Smith-Jones" not in cen2 and "Alice Smith" not in cen2.replace("Alice Smith-Jones", ""))
        chk("roster/near-miss-preserved", "Alicia Smithson" in cen2)
        chk("roster/round-trip", roster.restore(cen2) == "for Alice Smith-Jones and Alice Smith, not Alicia Smithson")
        chk("roster/self-test", roster.self_test() == [])

        chk("self-test/clean", censor.Censor(key, os.path.join(tmp, "v2.json")).self_test() == [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all censor controls passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
