"""Proof that temporal voting beats single-frame OCR.

The headline number in the pitch deck comes from `test_voting_beats_single_frame`.
It is a simulation, not a field trial, and the docstring says so — but it
demonstrates the mechanism honestly on controlled noise, and the same
harness runs against real labelled crops by swapping the generator.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edge.plate_norm import is_valid, normalise, repair          # noqa: E402
from edge.voting import PlateRead, TrackAccumulator, frame_quality  # noqa: E402

CONFUSIONS = {"0": "O", "O": "0", "1": "I", "I": "1", "8": "B", "B": "8",
              "5": "S", "S": "5", "2": "Z", "Z": "2", "6": "G", "G": "6"}


def noisy(plate: str, error_rate: float, rng: random.Random) -> tuple[str, list[float]]:
    """Corrupt a plate the way real OCR does: mostly confusion pairs."""
    out, confs = [], []
    for ch in plate:
        if rng.random() < error_rate:
            out.append(CONFUSIONS.get(ch, rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789")))
            confs.append(rng.uniform(0.30, 0.65))
        else:
            out.append(ch)
            confs.append(rng.uniform(0.85, 0.99))
    return "".join(out), confs


# --- normalisation & repair ------------------------------------------

def test_normalise_strips_separators():
    assert normalise("mh 12-de 1433") == "MH12DE1433"
    assert normalise("  hr26 dk 8337 ") == "HR26DK8337"


def test_valid_formats():
    assert is_valid("MH12DE1433")
    assert is_valid("DL8CAF5030")
    assert is_valid("22BH1234AA")      # Bharat series
    assert not is_valid("XX99ZZ1234")  # XX is not a real state code
    assert not is_valid("MH12DE143")   # too few digits


def test_repair_fixes_positional_confusions():
    # O in a digit slot must become 0; 8 in a letter slot must become B.
    assert repair("MH12DE143O")[0] == "MH12DE1430"
    assert repair("DL8CAF5O30")[0] == "DL8CAF5030"


def test_repair_refuses_to_invent():
    """A string with no valid interpretation is returned untouched.

    Returning a confident-looking wrong plate is the worst failure this
    system can have, because nothing downstream can detect it.
    """
    garbage = "ZZZZZZZZ"
    assert repair(garbage) == (garbage, False)


# --- voting mechanics -------------------------------------------------

def test_majority_wins_over_noise():
    acc = TrackAccumulator("cam-01:7")
    for _ in range(9):
        acc.add(PlateRead("MH12DE1433", [0.95] * 10, quality=0.9))
    for _ in range(2):                       # two badly wrong frames
        acc.add(PlateRead("MH12DE1455", [0.40] * 10, quality=0.3))
    r = acc.consensus()
    assert r.plate == "MH12DE1433"
    assert r.decision == "auto_accept"


def test_low_confidence_goes_to_review_not_published():
    """The core safety property: uncertainty must never be published."""
    acc = TrackAccumulator("cam-02:3")
    acc.add(PlateRead("MH12DE1433", [0.5] * 10, quality=0.3))
    acc.add(PlateRead("MH12DE1438", [0.5] * 10, quality=0.3))
    acc.add(PlateRead("MH12OE1433", [0.5] * 10, quality=0.3))
    r = acc.consensus()
    assert r.decision in ("review", "discard")
    assert r.confidence < 0.90


def test_quality_weighting_beats_raw_count():
    """Three blurry frames must not outvote two clean ones."""
    acc = TrackAccumulator("cam-03:9")
    for _ in range(2):
        acc.add(PlateRead("HR26DK8337", [0.97] * 10, quality=0.95))
    for _ in range(3):
        acc.add(PlateRead("HR26DK8887", [0.45] * 10, quality=0.08))
    assert acc.consensus().plate == "HR26DK8337"


def test_single_frame_track_is_not_trusted():
    acc = TrackAccumulator("cam-04:1")
    acc.add(PlateRead("PB65AR4412", [0.93] * 10, quality=0.8))
    r = acc.consensus()
    assert r.frame_count == 1
    assert r.decision != "auto_accept"       # one look is never enough


def test_stability_allows_early_exit():
    acc = TrackAccumulator("cam-05:2")
    for _ in range(5):
        acc.add(PlateRead("DL8CAF5030", [0.96] * 10, quality=0.9))
    assert acc.stable is True


def test_empty_accumulator_is_safe():
    assert TrackAccumulator("cam-06:0").consensus().decision == "discard"


def test_frame_quality_penalises_tiny_blurred_crops():
    good = frame_quality(bbox_area_px=6000, sharpness=200, aspect=2.0, detector_conf=0.95)
    tiny = frame_quality(bbox_area_px=200, sharpness=200, aspect=2.0, detector_conf=0.95)
    blur = frame_quality(bbox_area_px=6000, sharpness=8, aspect=2.0, detector_conf=0.95)
    skew = frame_quality(bbox_area_px=6000, sharpness=200, aspect=0.6, detector_conf=0.95)
    assert good > tiny and good > blur and good > skew


# --- the headline claim ----------------------------------------------

def test_voting_beats_single_frame():
    """Simulated 30-frame tracks at a 12% per-character error rate.

    This measures the voting mechanism under controlled noise; it is not
    a field accuracy figure. Report it as such — a judge who thinks you
    are claiming 99% on real Indian roads from a simulation will be right
    to push back.
    """
    rng = random.Random(42)
    plates = ["MH12DE1433", "DL8CAF5030", "HR26DK8337", "PB65AR4412",
              "KA05MN7788", "TN22BC9021", "UP32EE1005", "RJ14CV2210"]

    single_ok = single_total = 0
    voted_ok = voted_published = 0

    for plate in plates:
        for _ in range(40):                      # 40 simulated vehicle passes
            acc = TrackAccumulator("t")
            for _ in range(30):                  # 30 frames per pass
                text, confs = noisy(plate, 0.12, rng)
                acc.add(PlateRead(text, confs, quality=rng.uniform(0.5, 1.0)))
                single_total += 1
                if repair(text)[0] == plate:
                    single_ok += 1
            r = acc.consensus()
            if r.decision == "auto_accept":
                voted_published += 1
                if r.plate == plate:
                    voted_ok += 1

    single_acc = single_ok / single_total
    # Precision on the auto-accepted set — the number that actually matters,
    # because it is what the system publishes as fact.
    voted_precision = voted_ok / voted_published if voted_published else 0.0
    coverage = voted_published / (len(plates) * 40)

    print(f"\n  single-frame accuracy : {single_acc:.4f}")
    print(f"  voted precision       : {voted_precision:.4f}")
    print(f"  auto-accept coverage  : {coverage:.4f}")

    assert single_acc < 0.75, "sanity: per-frame noise should be substantial"
    assert voted_precision > 0.99, "voting must be near-perfect on what it publishes"
    assert coverage > 0.80, "and it must still publish most vehicles"


# --- regressions found on real Indian footage -------------------------

def test_rto_zero_is_not_a_valid_district():
    """KL-0-ZBA-5252 is structurally legal but not a real plate.

    Found in production output: OCR misread the 7 in KL07BA5252 as Z, and
    the permissive Indian regex accepted the result as well-formed, which
    let a wrong plate reach auto-accept.
    """
    assert not is_valid("KL0ZBA5252")
    assert is_valid("KL07BA5252")
    assert is_valid("DL3CBJ1384")      # single-digit RTO of 3 is fine


def test_few_frames_cannot_reach_auto_accept():
    """Five agreeing frames are not twenty frames of evidence."""
    acc = TrackAccumulator("cam-01:9")
    for _ in range(5):
        acc.add(PlateRead("KL07BA5252", [0.99] * 10, quality=1.0))
    r = acc.consensus()
    assert r.decision == "review", f"5 frames should not auto-accept (got {r.confidence})"

    acc2 = TrackAccumulator("cam-01:10")
    for _ in range(20):
        acc2.add(PlateRead("KL07BA5252", [0.99] * 10, quality=1.0))
    assert acc2.consensus().decision == "auto_accept"
