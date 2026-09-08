"""Temporal voting — the accuracy engine.

The honest claim
----------------
Single-frame plate OCR peaks around 95-98% in the field. Mud, rain,
glare, motion blur and shallow angles are physical facts, not model
defects. What raises accuracy is that a tracked vehicle gives us *many*
looks at the same plate, and the errors between frames are largely
independent: a glare artefact in frame 7 is not present in frame 22.
Accumulating per-character evidence across those frames drives the
probability of a wrong *consensus* down sharply.

"Production 100%" is therefore an operational property, not a model
metric. It means: the system never publishes a plate it is not sure
about. Above the auto-accept threshold it publishes; below it, the read
goes to a human. The measurable claim is precision on the auto-accepted
set, plus the coverage that set represents — both reported, never one
without the other.

What this module does NOT do
----------------------------
It does not invent certainty. A three-frame track of a filthy plate
stays low-confidence and goes to review, which is the correct outcome.
"""
from __future__ import annotations

import math
import os
from collections import defaultdict
from dataclasses import dataclass, field

from .plate_norm import is_valid, normalise, repair, slot_classes

# A vote needs enough looks to be meaningful. Below this the result is
# reported but heavily discounted.
MIN_FRAMES = 3
# Frames required before a vote gets full credit for its evidence.
#
# Tuned against real footage, where this was originally MIN_FRAMES and let
# a 5-frame consensus reach 0.995 — it published KL0ZBA5252 as fact when
# the true plate was KL07BA5252. Five agreeing frames are not the same
# evidence as twenty, and the confidence must say so.
FULL_EVIDENCE_FRAMES = int(os.getenv("NETRA_FULL_EVIDENCE_FRAMES", "8"))
# Frames below this quality are not allowed to vote at all.
#
# Weighting a hopeless frame down is not the same as excluding it: on real
# footage the long tail of tiny, motion-blurred crops still shifted the
# per-position margins and dragged a correct 28-frame consensus from 0.93
# to 0.82. Evidence that bad is not weak evidence, it is noise.
MIN_READ_QUALITY = float(os.getenv("NETRA_MIN_READ_QUALITY", "0.45"))
# Confidence bands. These mirror the API's config so the worker and the
# server agree on what "accepted" means.
AUTO_ACCEPT = 0.90
REVIEW_MIN = 0.55


@dataclass
class PlateRead:
    """One frame's OCR output for one tracked vehicle."""
    text: str
    char_confidences: list[float]
    quality: float = 1.0          # frame quality weight, 0..1
    timestamp: str = ""

    def __post_init__(self) -> None:
        self.text = normalise(self.text)


@dataclass
class VoteResult:
    plate: str
    confidence: float
    frame_count: int
    candidates: list[tuple[str, float]]
    format_valid: bool
    was_repaired: bool
    decision: str                 # auto_accept | review | discard
    per_position: list[float] = field(default_factory=list)


def frame_quality(bbox_area_px: float, sharpness: float, aspect: float,
                  detector_conf: float) -> float:
    """Weight a frame's vote by how much the image deserves to be trusted.

    Components, each mapped to 0..1:
      area       — a 20 px wide plate carries almost no information
      sharpness  — variance of Laplacian; low means motion blur
      aspect     — width/height of the crop. Plate shapes vary by region
                   (Indian single-row ~2:1 up to UK/EU ~5:1), so anything
                   inside that band scores full marks and only genuinely
                   wrong shapes — a square crop, or a sliver — are
                   penalised. Scoring a hard 2:1 optimum would have given
                   every real UK plate a quality of zero.
      detector   — the plate detector's own confidence

    Multiplicative rather than averaged: a frame that is catastrophically
    bad on any single axis should not be rescued by the other three.
    """
    area_score = min(1.0, bbox_area_px / 4000.0)
    sharp_score = min(1.0, sharpness / 120.0)
    if aspect <= 0:
        aspect_score = 0.0
    elif 1.8 <= aspect <= 5.5:
        aspect_score = 1.0
    elif aspect < 1.8:
        aspect_score = max(0.0, aspect / 1.8)
    else:
        aspect_score = max(0.0, 1.0 - (aspect - 5.5) / 4.0)
    q = area_score * sharp_score * aspect_score * max(0.0, min(1.0, detector_conf))
    # Fourth root keeps the weight from collapsing to ~0 for merely
    # mediocre frames, which would waste usable evidence.
    return round(q ** 0.25, 4)


class TrackAccumulator:
    """Collects reads for ONE track_id and produces a consensus plate.

    One accumulator per tracked vehicle. The tracker's identity guarantee
    is what makes voting valid: if the track is wrong, we are averaging
    two different cars' plates together, which is worse than not voting
    at all. That is why a tracker with strong ID stability matters more
    here than one with marginally better box accuracy.
    """

    def __init__(self, track_id: str, camera_id: str = "") -> None:
        self.track_id = track_id
        self.camera_id = camera_id
        self.reads: list[PlateRead] = []
        self.rejected = 0          # frames dropped for poor quality

    # --- collection ---------------------------------------------------

    def add(self, read: PlateRead) -> None:
        if read.text and read.quality >= MIN_READ_QUALITY:
            self.reads.append(read)
        elif read.text:
            self.rejected += 1

    def __len__(self) -> int:
        return len(self.reads)

    @property
    def stable(self) -> bool:
        """True when further frames are unlikely to change the answer.

        Lets the worker stop OCR-ing a vehicle it has already read
        unanimously — real compute savings on a 4 GB GPU.
        """
        if len(self.reads) < 5:
            return False
        recent = [r.text for r in self.reads[-5:]]
        return len(set(recent)) == 1 and is_valid(recent[0])

    # --- consensus ----------------------------------------------------

    def _dominant_length(self) -> int:
        """Quality-weighted modal length.

        Reads of different lengths cannot be aligned position-by-position,
        and the usual cause of a short read is a truncated crop. Rather
        than attempt edit-distance alignment (which happily aligns a
        missing character to the wrong column), we pick the best-supported
        length and let the other reads inform only the candidate list.
        """
        weights: dict[int, float] = defaultdict(float)
        for r in self.reads:
            w = max(r.quality, 0.01) * (1.0 + 0.5 * is_valid(r.text))
            weights[len(r.text)] += w
        return max(weights.items(), key=lambda kv: kv[1])[0]

    def consensus(self) -> VoteResult:
        if not self.reads:
            return VoteResult("", 0.0, 0, [], False, False, "discard")

        target_len = self._dominant_length()
        voting = [r for r in self.reads if len(r.text) == target_len]
        skeleton = slot_classes(target_len)

        # --- per-position weighted vote ---
        columns: list[dict[str, float]] = [defaultdict(float) for _ in range(target_len)]
        for r in voting:
            for i, ch in enumerate(r.text):
                # Per-character confidence when OCR gave us one, else the
                # read's mean.
                cc = (r.char_confidences[i]
                      if i < len(r.char_confidences) else
                      (sum(r.char_confidences) / len(r.char_confidences)
                       if r.char_confidences else 0.5))
                w = max(0.01, r.quality) * max(0.01, cc)
                # Grammar prior: a character in the right class for its
                # slot is mildly favoured. Mild on purpose — a strong
                # prior would manufacture plausible-looking wrong plates.
                if skeleton:
                    expected_alpha = skeleton[i] == "A"
                    if ch.isalpha() == expected_alpha:
                        w *= 1.25
                columns[i][ch] += w

        chars: list[str] = []
        margins: list[float] = []
        for col in columns:
            if not col:
                chars.append("?")
                margins.append(0.0)
                continue
            ranked = sorted(col.items(), key=lambda kv: -kv[1])
            total = sum(col.values())
            best_ch, best_w = ranked[0]
            runner_w = ranked[1][1] if len(ranked) > 1 else 0.0
            chars.append(best_ch)
            # Share of evidence for the winner, penalised by how close the
            # runner-up is. A 51/49 split must not read as "51% sure".
            share = best_w / total if total else 0.0
            separation = (best_w - runner_w) / best_w if best_w else 0.0
            margins.append(share * (0.5 + 0.5 * separation))

        raw = "".join(chars)
        plate, was_repaired = repair(raw)
        valid = is_valid(plate)

        # --- combine into one confidence ---
        # Geometric mean, not arithmetic: one hopeless character must drag
        # the whole plate down, because one wrong character is a wrong
        # plate. An arithmetic mean would let seven strong positions hide
        # a coin-flip in the eighth.
        if margins:
            logs = [math.log(max(m, 1e-6)) for m in margins]
            positional = math.exp(sum(logs) / len(logs))
        else:
            positional = 0.0

        # Few frames means little independent evidence. Saturating at
        # MIN_FRAMES made three agreeing frames look as certain as twenty,
        # which is how a wrong plate reached auto-accept on real footage.
        frame_factor = min(1.0, len(voting) / FULL_EVIDENCE_FRAMES) ** 0.5
        # A well-formed plate is genuinely more likely to be right, but
        # the bonus is small: format validity is evidence, not proof.
        format_factor = 1.0 if valid else 0.82
        # Repair means we changed characters the model actually saw.
        repair_penalty = 0.95 if was_repaired else 1.0

        confidence = positional * frame_factor * format_factor * repair_penalty
        confidence = round(min(confidence, 0.995), 4)

        # --- candidate list, for the review UI and AI arbitration ---
        totals: dict[str, float] = defaultdict(float)
        for r in self.reads:
            totals[repair(r.text)[0]] += max(0.01, r.quality)
        grand = sum(totals.values()) or 1.0
        candidates = sorted(
            ((p, round(w / grand, 4)) for p, w in totals.items()),
            key=lambda kv: -kv[1])[:5]
        if plate not in dict(candidates):
            candidates.insert(0, (plate, round(confidence, 4)))
            candidates = candidates[:5]

        if confidence >= AUTO_ACCEPT and valid:
            decision = "auto_accept"
        elif confidence >= REVIEW_MIN:
            decision = "review"
        else:
            decision = "discard"

        return VoteResult(
            plate=plate,
            confidence=confidence,
            frame_count=len(voting),
            candidates=candidates,
            format_valid=valid,
            was_repaired=was_repaired,
            decision=decision,
            per_position=[round(m, 4) for m in margins],
        )
