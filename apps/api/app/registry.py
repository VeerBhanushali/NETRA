"""Vehicle registration lookup — owner, RC, insurance, PUC and fitness.

The authoritative source in India is VAHAN (MoRTH/NIC). Access to it is
granted to police and transport departments under an MoU; it is not
something a team can obtain for a hackathon, and pretending otherwise in
a demo would be a lie a judge can catch in one question.

So this module is an *adapter* with three providers:

  mock      deterministic synthetic records, clearly labelled DEMO DATA.
            The default, and the only one that runs out of the box.
  http      a commercial RC-verification API (Surepass, Attestr, Signzy
            and similar resell VAHAN-derived data under contract). Set
            NETRA_RC_URL and NETRA_RC_KEY and it takes over.
  vahan     the real thing, once an MoU exists. Same shape, different
            endpoint and auth. Not implemented; documented so the seam is
            visible.

What this module deliberately does NOT do is scrape parivahan.gov.in.
That site is CAPTCHA- and OTP-gated precisely to prevent bulk lookup, and
defeating that would be both a terms violation and the exact abuse the
privacy design of this system exists to prevent.

PRIVACY POSTURE
---------------
Owner data is personal data under the DPDP Act 2023. NETRA's sightings
database holds none of it and this module does not change that:

  * lookups happen on demand, never as part of ingestion
  * every lookup requires a stated reason and is written to audit_log
  * results are cached in memory with a short TTL, never written to a table
  * the response says where the data came from, so an operator is never
    left guessing whether they are looking at a real record

A plate is a public identifier. The person attached to it is not.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

PROVIDER = os.getenv("NETRA_RC_PROVIDER", "mock").lower()
RC_URL = os.getenv("NETRA_RC_URL", "")
RC_KEY = os.getenv("NETRA_RC_KEY", "")
# Providers disagree about the auth header, the request field name and
# how deep the payload is nested. Rather than a class per vendor, the
# handful of things that actually differ are configuration — so a new
# provider is four environment variables, not a code change.
RC_AUTH_HEADER = os.getenv("NETRA_RC_AUTH_HEADER", "authorization")
RC_AUTH_FORMAT = os.getenv("NETRA_RC_AUTH_FORMAT", "Bearer {key}")
RC_FIELD = os.getenv("NETRA_RC_FIELD", "id_number")
RC_ENVELOPE = os.getenv("NETRA_RC_ENVELOPE", "data")
RC_METHOD = os.getenv("NETRA_RC_METHOD", "POST").upper()
# Short: an owner record fetched for one investigation should not still be
# sitting in memory an hour later serving a different one.
CACHE_TTL_S = float(os.getenv("NETRA_RC_CACHE_TTL", "300"))

_cache: dict[str, tuple[float, dict]] = {}


# --- mock provider ---------------------------------------------------------

# Enough to be recognisably Indian and internally consistent. Names are
# invented; any resemblance to a real registration is coincidental, which
# is the point — no real person's details are in this repository.
_MAKES = [
    ("Maruti Suzuki", "Swift VDI", "Diesel", "LMV"),
    ("Hyundai", "i20 Sportz", "Petrol", "LMV"),
    ("Tata", "Nexon EV", "Electric(BOV)", "LMV"),
    ("Mahindra", "Bolero Neo", "Diesel", "LMV"),
    ("Honda", "Activa 6G", "Petrol", "MCWG"),
    ("Bajaj", "Pulsar 150", "Petrol", "MCWG"),
    ("Ashok Leyland", "Dost+", "Diesel", "LGV"),
    ("Toyota", "Innova Crysta", "Diesel", "LMV"),
]
_SURNAMES = ["Nair", "Sharma", "Patel", "Reddy", "Iyer", "Khan", "Singh",
             "Das", "Menon", "Joshi", "Pillai", "Verma"]
_FIRST = ["Arun", "Priya", "Rahul", "Meera", "Vikram", "Anjali", "Suresh",
          "Kavya", "Imran", "Neha", "Rajesh", "Divya"]
_RTO_CITY = {
    "KL": "Kerala", "MH": "Maharashtra", "DL": "Delhi", "HR": "Haryana",
    "KA": "Karnataka", "TN": "Tamil Nadu", "UP": "Uttar Pradesh",
    "GJ": "Gujarat", "RJ": "Rajasthan", "WB": "West Bengal", "PB": "Punjab",
}


def _mock_record(plate: str) -> dict:
    """A stable synthetic record for a plate.

    Derived from a hash of the plate so the same plate always returns the
    same owner across restarts — a demo where the owner changes each time
    you open the page is worse than no demo.
    """
    h = hashlib.sha256(plate.encode()).digest()
    make, model, fuel, cls = _MAKES[h[0] % len(_MAKES)]
    first, last = _FIRST[h[1] % len(_FIRST)], _SURNAMES[h[2] % len(_SURNAMES)]
    state = _RTO_CITY.get(plate[:2], "Unknown")

    # Registration 1-14 years ago, documents expiring around now so the
    # demo shows both valid and expired states without being rigged.
    now = time.time()
    reg = now - (365 * 86400) * (1 + h[3] % 14) - (h[4] % 365) * 86400
    puc_days = (h[5] % 400) - 100          # -100..+300 days from today
    ins_days = (h[6] % 500) - 120
    fit_days = (h[7] % 900) - 90

    def iso(ts: float) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(ts))

    return {
        "plate": plate,
        "registered_at": iso(reg),
        "rto": f"{plate[:4]} {state}",
        "state": state,
        "owner_name": f"{first} {last}",
        # Masked by default. Full PII behind a second, separately audited
        # step is the pattern real police systems use, and the one this
        # would follow if it were ever wired to VAHAN.
        "owner_masked": f"{first[0]}. {last}",
        "father_name": f"{_FIRST[h[8] % len(_FIRST)]} {last}",
        "address_masked": f"{state}, pin {30 + h[9] % 60}{h[10] % 10}{h[11] % 10}{h[12] % 10}{h[13] % 10}",
        "owner_serial": 1 + h[14] % 3,
        "maker": make,
        "model": model,
        "fuel": fuel,
        "vehicle_class": cls,
        "colour": ["White", "Silver", "Red", "Blue", "Grey", "Black"][h[15] % 6],
        "chassis_masked": f"MA{h[16]:02X}****{h[17]:02X}{h[18]:02X}",
        "engine_masked": f"{h[19]:02X}****{h[20]:02X}",
        "puc": {"valid_until": iso(now + puc_days * 86400),
                "status": "valid" if puc_days > 0 else "expired",
                "certificate_no": f"PUC{h[21]:02X}{h[22]:02X}{h[23]:02X}"},
        "insurance": {"valid_until": iso(now + ins_days * 86400),
                      "status": "valid" if ins_days > 0 else "expired",
                      "insurer": ["ICICI Lombard", "New India Assurance",
                                  "HDFC ERGO", "Bajaj Allianz"][h[24] % 4]},
        "fitness": {"valid_until": iso(now + fit_days * 86400),
                    "status": "valid" if fit_days > 0 else "expired"},
        "blacklist": "none",
        "rc_status": "ACTIVE",
    }


# --- http provider ---------------------------------------------------------

def _http_record(plate: str) -> dict:
    """Query a commercial RC-verification API.

    Providers differ in field names, so the mapping lives here rather than
    leaking into the router or the UI. Only the shape below is a contract.
    """
    headers = {"content-type": "application/json",
               RC_AUTH_HEADER: RC_AUTH_FORMAT.format(key=RC_KEY)}

    if RC_METHOD == "GET":
        sep = "&" if "?" in RC_URL else "?"
        req = urllib.request.Request(
            f"{RC_URL}{sep}{urllib.parse.urlencode({RC_FIELD: plate})}",
            headers=headers, method="GET")
    else:
        req = urllib.request.Request(
            RC_URL, json.dumps({RC_FIELD: plate}).encode(), headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:                  # noqa: PERF203
        detail = exc.read().decode(errors="replace")[:300]
        # 401/403 is almost always a bad or unactivated key, and saying so
        # saves an hour of suspecting the plate.
        raise RuntimeError(
            f"RC provider returned {exc.code}: {detail}"
            + ("  (check NETRA_RC_KEY and that the plan is active)"
               if exc.code in (401, 403) else "")) from exc

    d = raw
    for part in RC_ENVELOPE.split("."):
        if isinstance(d, dict) and part in d:
            d = d[part]
    if not isinstance(d, dict):
        raise RuntimeError(
            f"RC response had no '{RC_ENVELOPE}' object — set NETRA_RC_ENVELOPE "
            f"to the path holding the record. Got keys: {list(raw)[:8]}")
    def pick(*names: str):
        """First non-empty value among the names vendors actually use."""
        for n in names:
            v = d.get(n)
            if v not in (None, "", "NA", "N/A"):
                return v
        return None

    owner = pick("owner_name", "ownerName", "owner", "name")
    puc_to = pick("pucc_upto", "puccUpto", "pollution_upto", "puc_upto")
    ins_to = pick("insurance_upto", "insuranceUpto", "insurance_validity")
    fit_to = pick("fit_up_to", "fitUpTo", "fitness_upto", "rc_expiry_date")

    return {
        "plate": plate,
        "registered_at": pick("registration_date", "registrationDate", "reg_date"),
        "rto": pick("registered_at", "rto", "rto_name", "registeredAt"),
        "state": pick("state", "state_name"),
        "owner_name": owner,
        "owner_masked": _mask_name(owner),
        "father_name": pick("father_name", "fatherName"),
        "address_masked": _mask_address(
            pick("present_address", "permanent_address", "address")),
        "owner_serial": pick("owner_number", "ownerNumber", "owner_serial_no"),
        "maker": pick("maker_description", "maker", "makerDescription"),
        "model": pick("maker_model", "model", "makerModel"),
        "fuel": pick("fuel_type", "fuelType", "fuel"),
        "vehicle_class": pick("vehicle_class_description", "vehicleClass", "class"),
        "colour": pick("colour", "color"),
        "chassis_masked": _mask_id(pick("vehicle_chasi_number", "chassis_number",
                                        "chassisNumber")),
        "engine_masked": _mask_id(pick("vehicle_engine_number", "engine_number",
                                       "engineNumber")),
        "puc": {"valid_until": puc_to, "status": _status(puc_to),
                "certificate_no": pick("pucc_number", "puccNumber")},
        "insurance": {"valid_until": ins_to, "status": _status(ins_to),
                      "insurer": pick("insurance_company", "insuranceCompany")},
        "fitness": {"valid_until": fit_to, "status": _status(fit_to)},
        "blacklist": pick("blacklist_status", "blacklistStatus") or "none",
        "rc_status": pick("rc_status", "rcStatus", "status"),
    }


def _mask_name(v: str | None) -> str | None:
    """Initial plus surname. The console shows this; the full name needs a
    second, separately audited reveal."""
    if not v:
        return None
    parts = str(v).split()
    return f"{parts[0][0]}. {parts[-1]}" if len(parts) > 1 else parts[0]


def _mask_address(v: str | None) -> str | None:
    """Locality and PIN only. A full postal address on screen is how a
    surveillance console becomes a stalking tool."""
    if not v:
        return None
    s = str(v)
    pin = "".join(c for c in s[-8:] if c.isdigit())[-6:]
    tail = s.split(",")[-2:] if "," in s else [s[-24:]]
    return ", ".join(p.strip() for p in tail if p.strip()) + (f", pin {pin}" if pin else "")


def _mask_id(v: str | None) -> str | None:
    """Chassis and engine numbers identify the vehicle uniquely; the last
    four are enough to verify a match without publishing the whole thing."""
    if not v:
        return None
    s = str(v)
    return f"{'*' * max(0, len(s) - 4)}{s[-4:]}" if len(s) > 4 else s


def _status(date_str: str | None) -> str:
    if not date_str:
        return "unknown"
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return "valid" if time.mktime(time.strptime(date_str, fmt)) > time.time() \
                else "expired"
        except ValueError:
            continue
    return "unknown"


# --- public API ------------------------------------------------------------

def available() -> dict[str, Any]:
    """What this deployment can actually do — the config endpoint uses this."""
    return {
        "provider": PROVIDER,
        "configured": PROVIDER == "mock" or bool(RC_URL and RC_KEY),
        "is_demo_data": PROVIDER == "mock",
        "note": {
            "mock": "Synthetic records generated from the plate. NOT real "
                    "registration data — for demonstration only.",
            "http": f"Commercial RC-verification API at {RC_URL or 'unset'}.",
            "vahan": "Direct VAHAN access requires an MoU with the state "
                     "transport department; not implemented.",
        }.get(PROVIDER, "unknown provider"),
    }


def lookup(plate: str) -> dict:
    """Fetch a registration record. Caller must have already audited."""
    plate = plate.upper().replace(" ", "").replace("-", "")

    hit = _cache.get(plate)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return {**hit[1], "cached": True}

    if PROVIDER == "http":
        if not (RC_URL and RC_KEY):
            raise RuntimeError(
                "NETRA_RC_PROVIDER=http but NETRA_RC_URL/NETRA_RC_KEY are unset")
        record = _http_record(plate)
    elif PROVIDER == "vahan":
        raise RuntimeError(
            "Direct VAHAN integration is not implemented. It requires an MoU "
            "with the state transport department and NIC-issued credentials.")
    else:
        record = _mock_record(plate)

    record["source"] = PROVIDER
    record["is_demo_data"] = PROVIDER == "mock"
    record["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _cache[plate] = (time.time(), record)
    return {**record, "cached": False}
