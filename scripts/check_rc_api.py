"""Test a real RC-lookup API key before pointing NETRA at it.

    set NETRA_RC_PROVIDER=http
    set NETRA_RC_URL=https://<provider>/rc/rc-full
    set NETRA_RC_KEY=<your key>
    python scripts/check_rc_api.py KL07BA5252

Prints the raw provider response alongside the record NETRA maps it to,
so a field that lands empty is obvious immediately — that is nearly
always a name mismatch fixed with NETRA_RC_FIELD or NETRA_RC_ENVELOPE
rather than a broken key.

Real registration data is personal data. Test against a plate you own or
one your engagement authorises. Every lookup through the API itself is
written to audit_log; this script bypasses the console, so it is for
configuration only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                      # noqa: BLE001
        pass


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    plate = sys.argv[1].upper().replace(" ", "").replace("-", "")

    from app import registry

    print(f"provider      {registry.PROVIDER}")
    print(f"url           {registry.RC_URL or '(unset)'}")
    print(f"key           {'set (' + str(len(registry.RC_KEY)) + ' chars)' if registry.RC_KEY else '(unset)'}")
    print(f"request field {registry.RC_FIELD}   envelope {registry.RC_ENVELOPE}")
    print(f"auth header   {registry.RC_AUTH_HEADER}: {registry.RC_AUTH_FORMAT.format(key='…')}")
    print()

    if registry.PROVIDER == "mock":
        print("Provider is 'mock' — this returns SYNTHETIC data, not a real")
        print("registration. Set NETRA_RC_PROVIDER=http with a real key to")
        print("check a live provider.\n")

    try:
        record = registry.lookup(plate)
    except Exception as exc:                               # noqa: BLE001
        print(f"FAILED: {exc}")
        return 1

    empty = [k for k, v in record.items() if v in (None, "", {})]
    print(json.dumps(record, indent=2, ensure_ascii=False))
    print()
    if empty:
        print("Empty fields: " + ", ".join(empty))
        print("If most fields are empty the envelope path is probably wrong.")
        print("Set NETRA_RC_ENVELOPE to where the record sits, e.g. 'data' or")
        print("'result.vehicle', and NETRA_RC_FIELD to the request key the")
        print("provider expects (id_number, reg_no, vehicleNumber, ...).")
    else:
        print("All fields populated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
