"""Indian number plate grammar: normalisation, validation and repair.

The plate format is a strong prior. Knowing that position 0-1 must be
letters and the last four must be digits lets us resolve the classic OCR
confusions (O/0, I/1, B/8, S/5, Z/2, G/6) by *position* rather than by
guessing — a 0 in a letter slot is almost certainly an O, and an O in a
digit slot is almost certainly a 0.

The grammar is used as a prior, never as a hard filter. Real plates are
damaged, non-standard, and occasionally illegal; discarding everything
that fails the regex would silently blind the system to exactly the
vehicles most worth watching.
"""
from __future__ import annotations

import os
import re

# Which plate grammar to enforce.
#   IN  — Indian Bharat/RTO format (the deployment target)
#   UK  — current UK format, used by the public ANPR test footage
#   ANY — any plausible alphanumeric plate, 5-9 characters
#
# This is not a convenience switch. Running the Indian grammar against
# non-Indian footage would mark every correct read as malformed, penalise
# its confidence and dump the whole stream into the review queue — the
# system would look broken when it was working perfectly. Set ANY when
# processing foreign sample video.
REGION = os.getenv("NETRA_PLATE_REGION", "IN").upper()

# Standard format: 2 letter state + 1-2 digit RTO + 1-3 letter series + 4 digits
RE_STANDARD = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$")
# Bharat series: 2 digit year + 'BH' + 4 digits + 1-2 letters
RE_BH = re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$")
# Current UK format: 2 area letters + 2 age digits + 3 random letters,
# e.g. KH06KSU. Fixed length 7, which makes positional repair strong.
RE_UK = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z]{3}$")
# Generic: 5-9 alphanumerics containing at least one letter and one digit.
# The mixed requirement rejects OCR picking up a road sign or a bare number.
RE_GENERIC = re.compile(r"^(?=.*[A-Z])(?=.*[0-9])[A-Z0-9]{5,9}$")

STATE_CODES = {
    "AN", "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "DN", "GA", "GJ",
    "HP", "HR", "JH", "JK", "KA", "KL", "LA", "LD", "MH", "ML", "MN", "MP",
    "MZ", "NL", "OD", "OR", "PB", "PY", "RJ", "SK", "TN", "TR", "TS", "UK",
    "UP", "WB",
}

# Digit that a letter is most often misread as, and vice versa.
LETTER_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1",
                   "Z": "2", "S": "5", "B": "8", "G": "6", "A": "4", "T": "7"}
DIGIT_TO_LETTER = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B",
                   "6": "G", "4": "A", "7": "T"}


def normalise(text: str) -> str:
    """Uppercase and strip everything that is not A-Z or 0-9.

    Handles the IND strip, state emblems, hyphens, spaces and the dirt
    that OCR reports as punctuation.
    """
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def is_valid(plate: str) -> bool:
    """True if the string is a well-formed plate for the active region."""
    if REGION == "ANY":
        return bool(RE_GENERIC.match(plate))
    if REGION == "UK":
        return bool(RE_UK.match(plate))
    if RE_BH.match(plate):
        return True
    m = RE_STANDARD.match(plate)
    if not m:
        return False
    if plate[:2] not in STATE_CODES:
        return False
    # RTO district codes start at 1. A single-digit "0" is not a real
    # district, and accepting it let a misread 7 slip through as
    # KL-0-ZBA-5252, which the regex happily called well-formed. Rejecting
    # it pushes the string into repair, where the digit slot is corrected.
    rto = re.match(r"^[A-Z]{2}([0-9]{1,2})", plate).group(1)
    if rto == "0":
        return False
    return True


def _templates(n: int) -> list[str]:
    """Character-class skeletons of length n, most likely first.

    'A' = letter slot, '9' = digit slot. Ordering matters: the first
    template that produces a valid plate wins, so the most common layouts
    are tried before the rare ones.
    """
    out = []
    if REGION == "UK":
        # Single fixed skeleton — which is exactly why positional repair
        # is so effective here: every slot's character class is known.
        return ["AA99AAA"] if n == 7 else []
    # Standard: 2 letters + (1-2) digits + (1-3) letters + 4 digits
    for rto in (2, 1):
        for series in (2, 1, 3):
            if 2 + rto + series + 4 == n:
                out.append("AA" + "9" * rto + "A" * series + "9999")
    # Bharat series
    for tail in (2, 1):
        if 2 + 2 + 4 + tail == n:
            out.append("99BH9999" + "A" * tail)
    return out


def repair(plate: str) -> tuple[str, bool]:
    """Coerce a string toward a valid plate using positional confusions.

    Returns (repaired, was_repaired). If nothing produces a valid plate,
    the original is returned untouched — a wrong "corrected" plate is far
    more dangerous than an obviously broken one, because it looks
    trustworthy.
    """
    plate = normalise(plate)
    if not plate or is_valid(plate):
        return plate, False

    # Positional repair only makes sense where the grammar fixes which
    # slots are letters and which are digits. With no regional grammar
    # there is nothing to repair toward, and guessing would corrupt
    # correct reads.
    if REGION == "ANY":
        return plate, False

    for template in _templates(len(plate)):
        out = []
        for ch, slot in zip(plate, template):
            if slot == "A" and ch.isdigit():
                out.append(DIGIT_TO_LETTER.get(ch, ch))
            elif slot == "9" and ch.isalpha():
                out.append(LETTER_TO_DIGIT.get(ch, ch))
            else:
                out.append(ch)
        candidate = "".join(out)
        if is_valid(candidate):
            return candidate, True

    return plate, False


def slot_classes(length: int) -> str | None:
    """The most likely character-class skeleton for a plate of this length,
    used to bias per-position voting toward the right character class."""
    t = _templates(length)
    return t[0] if t else None
