"""One realistic document, end to end: a helpdesk note with six kinds of PII in it.

This is the shape of thing the package exists for — prose written by a human, with an
identifier in nearly every clause. It is a regression test as much as a demo: every value
here must be caught, the round trip must be byte-exact, and the aliased text must be safe to
hand to a model.
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import censor  # noqa: E402

NOTE = """Summarize the following IT helpdesk note in two sentences. Do not include any personal user details or internal device information in your summary.

--- BEGIN HELPDESK NOTE ---
User Priya Nair (priya.nair@example.com, +65 8123 9876) called the IT desk because she could not connect to the office printer. 

Technician logs show her laptop (Hostname: DESKTOP-9941A, IP: 192.168.1.45, MAC: 00:1A:2B:3C:4D:5E) was connected to the guest Wi-Fi instead of the corporate network. The technician switched her profile back to the main corporate SSID, and printing resumed normally.
--- END NOTE ---"""

VALUES = {
    "name": "Priya Nair",
    "email": "priya.nair@example.com",
    "phone": "+65 8123 9876",
    "device": "DESKTOP-9941A",
    "ipv4": "192.168.1.45",
    "mac": "00:1A:2B:3C:4D:5E",
}

FAILS = []


def chk(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILS.append(name)
    return ok


def main():
    tmp = tempfile.mkdtemp(prefix="sampletest")
    try:
        cens = censor.Censor(os.path.join(tmp, "k.bin"), os.path.join(tmp, "v.json"),
                             names=["Priya Nair"])
        aliased, kinds = cens.censor_verified(NOTE)

        for kind, value in VALUES.items():
            chk(f"caught/{kind}", kinds.get(kind) == 1, f"kinds={kinds}")

        chk("egress/no-value-survives", all(v not in aliased for v in VALUES.values()),
            [v for v in VALUES.values() if v in aliased])
        chk("egress/round-trip-byte-exact", cens.restore(aliased) == NOTE)
        chk("egress/instruction-intact",
            "Summarize the following IT helpdesk note in two sentences." in aliased)
        chk("egress/prose-intact", "printing resumed normally" in aliased)
        chk("egress/no-false-positive",
            "guest Wi-Fi" in aliased and "corporate network" in aliased)

        chk("problems/none", cens.self_test() == [])

        # The same note as a PDF, which is the format that lies most easily: text outside the
        # page box is dropped by the extractor, so the fixture wraps like a real document.
        from fixtures_make import make_pdf
        import ingest
        pdf_path = os.path.join(tmp, "helpdesk.pdf")
        make_pdf(pdf_path, NOTE)
        extracted, fmt = ingest.extract(pdf_path)
        chk("pdf/extracted", fmt == "pdf" and "printing resumed" in extracted, extracted[:60])
        pdf_aliased, pdf_kinds = cens.censor_verified(extracted)
        chk("pdf/all-six-caught",
            all(pdf_kinds.get(k) == 1 for k in VALUES), f"{pdf_kinds}")
        chk("pdf/no-value-survives", all(v not in pdf_aliased for v in VALUES.values()))
        chk("pdf/round-trip", cens.restore(pdf_aliased) == extracted)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all sample-note controls passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
