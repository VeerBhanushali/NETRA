"""Road accident detection from vehicle trajectories.

STATUS: MEASURED AND FOUND INSUFFICIENT. Do not enable in a demo.
================================================================
Benchmarked against 6 real CCTV clips (5 accidents, 1 normal-traffic
control) from the CCTVAccidentDetection dataset. Counts of each candidate
signal:

    clip     label      hard-brake  stop-after-move  track-ends-at-speed
    FP2      NORMAL             30               15                    6
    V1       accident           20               10                    9
    V12      accident           18               12                    1
    V4       accident            1                0                   13
    V6       accident            0                0                    3
    V7       accident            3                0                    2

The NORMAL clip scores HIGHEST on braking and stopping, while three real
accidents score near zero. These signals do not separate the classes.

Why, concretely:
  * 2D box IoU is a poor proxy for contact. Perspective makes a distant
    vehicle overlap a near one constantly — the control clip produced 143
    overlaps, more than any accident clip.
  * In dense urban traffic, hard braking and stopping ARE the normal
    behaviour. The discriminator has to be *where* and *how* a vehicle
    stopped, which needs lane geometry we do not have.
  * Collisions are brief and often occluded; the tracker frequently loses
    the ID at the moment of impact, destroying the very history the
    deceleration test depends on.

The correct fix is a trained model (CCD or UCF-Crime + VideoMAE/RTFM),
not more hand-tuned thresholds — this is a case where learning genuinely
beats geometry. Tuning these constants until six clips pass would be
overfitting to six clips.

The module is kept because the tracking scaffolding, the evidence format
and the alert plumbing are all reusable by that model. The detector is
OFF unless NETRA_ENABLE_ACCIDENT=true is set explicitly.
"""

__doc_original__ = """Road accident detection from vehicle trajectories.

No new model. The tracker already gives us, for every vehicle, a box per
frame — and a collision has a geometric signature that a classifier has
to learn but we can simply measure:

  1. two vehicle boxes converge and overlap
  2. both lose most of their speed within a fraction of a second
  3. at least one stays stopped, in a travel lane, for several seconds

A CNN trained on accident stills learns "crumpled metal and a crowd",
which is why those models fire on parked cars and red trucks. Measuring
the physics is cheaper, runs on tracks we already compute, and — the part
that matters for a control room — produces an alert an operator can
interrogate: *these two vehicles, this closing speed, stopped this long.*

Requires a FIXED camera. On a moving dashcam every vehicle appears to
decelerate whenever the ego vehicle brakes, so `stationary_camera` must
be set false for those and only the collision test is used.
"""
from __future__ import annotations

import math
import os
from collections import deque
from dataclasses import dataclass, field

# --- thresholds -------------------------------------------------------
# Fraction of its own recent speed a vehicle must lose to count as an
# abrupt stop. 0.7 is well beyond normal braking at junction speeds.
DECEL_FRACTION = float(os.getenv("NETRA_ACC_DECEL", "0.70"))
# Box overlap (IoU) at which two vehicles are considered to have made
# contact. Deliberately low: cameras see collisions obliquely, so real
# contact often shows as partial overlap.
CONTACT_IOU = float(os.getenv("NETRA_ACC_IOU", "0.12"))
# How long a vehicle must remain still before a stop is an incident and
# not a traffic queue.
STOPPED_S = float(os.getenv("NETRA_ACC_STOPPED", "5.0"))
# Speed under which a vehicle counts as stopped, in px/s of box centre.
STOPPED_SPEED_PX = float(os.getenv("NETRA_ACC_STOP_SPEED", "6.0"))
# Frames of history kept per track.
HISTORY = 40
# A track must be seen this many times before its speed estimate is
# trustworthy — two frames of a new track say nothing about deceleration.
MIN_HISTORY = 6
# Don't re-raise the same incident continuously.
COOLDOWN_S = float(os.getenv("NETRA_ACC_COOLDOWN", "60"))
# Off by default: see the measured result in the module docstring. A
# detector that fires more on normal traffic than on accidents must not
# be able to reach an operator by accident.
ENABLED = os.getenv("NETRA_ENABLE_ACCIDENT", "false").lower() == "true"


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Track:
    """Recent motion of one tracked vehicle."""
    tid: int
    # (timestamp, centre_x, centre_y, box)
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY))
    stopped_since: float | None = None
    flagged: bool = False

    def update(self, t: float, box) -> None:
        cx = (box[0] + box[2]) / 2
        cy = (box[1] + box[3]) / 2
        self.history.append((t, cx, cy, box))

    @property
    def box(self):
        return self.history[-1][3] if self.history else None

    def speed(self, window: float = 0.5) -> float:
        """Centre speed in pixels/second over the last `window` seconds."""
        if len(self.history) < 2:
            return 0.0
        t_now, x_now, y_now, _ = self.history[-1]
        for t, x, y, _ in reversed(self.history):
            if t_now - t >= window:
                dt = t_now - t
                return math.hypot(x_now - x, y_now - y) / dt if dt > 0 else 0.0
        t0, x0, y0, _ = self.history[0]
        dt = t_now - t0
        return math.hypot(x_now - x0, y_now - y0) / dt if dt > 0 else 0.0

    def prior_speed(self, back: float = 2.0, window: float = 0.5) -> float:
        """Speed as it was `back` seconds ago — the baseline a sudden stop
        is measured against."""
        if len(self.history) < MIN_HISTORY:
            return 0.0
        t_now = self.history[-1][0]
        older = [h for h in self.history if t_now - h[0] >= back]
        if len(older) < 2:
            return 0.0
        t1, x1, y1, _ = older[-1]
        for t, x, y, _ in reversed(older):
            if t1 - t >= window:
                dt = t1 - t
                return math.hypot(x1 - x, y1 - y) / dt if dt > 0 else 0.0
        return 0.0


class AccidentDetector:
    """Watches one camera's tracks for collision signatures."""

    def __init__(self, camera_id: str, stationary_camera: bool = True) -> None:
        self.camera_id = camera_id
        self.stationary = stationary_camera
        self.tracks: dict[int, Track] = {}
        self.last_alert_at: float = -1e9

    def update(self, t: float, boxes: dict[int, tuple]) -> list[dict]:
        """Feed one frame's tracked boxes; return any incidents detected.

        `boxes` maps track id -> (x1, y1, x2, y2) in frame pixels.
        """
        for tid, box in boxes.items():
            self.tracks.setdefault(tid, Track(tid)).update(t, box)

        # Forget tracks that have left; their history is meaningless once
        # the vehicle is gone and keeping it leaks memory on a long run.
        for tid in [k for k, tr in self.tracks.items()
                    if tr.history and t - tr.history[-1][0] > 5.0]:
            self.tracks.pop(tid, None)

        if not ENABLED:
            return []
        if t - self.last_alert_at < COOLDOWN_S:
            return []

        incidents = self._collisions(t, boxes)
        if self.stationary:
            incidents += self._stopped_in_lane(t)

        if incidents:
            self.last_alert_at = t
        return incidents

    # --- signature 1: two vehicles converge, contact, and both stop ---
    def _collisions(self, t: float, boxes: dict[int, tuple]) -> list[dict]:
        out = []
        ids = [i for i in boxes if i in self.tracks
               and len(self.tracks[i].history) >= MIN_HISTORY]

        for n, a_id in enumerate(ids):
            for b_id in ids[n + 1:]:
                a, b = self.tracks[a_id], self.tracks[b_id]
                overlap = iou(boxes[a_id], boxes[b_id])
                if overlap < CONTACT_IOU:
                    continue

                # Both must have been moving, and both must have lost most
                # of that speed. One vehicle stopping behind another is
                # traffic; both stopping at the moment they touch is not.
                a_before, b_before = a.prior_speed(), b.prior_speed()
                a_now, b_now = a.speed(), b.speed()
                if a_before < 12 or b_before < 12:
                    continue
                a_drop = 1 - (a_now / a_before) if a_before else 0
                b_drop = 1 - (b_now / b_before) if b_before else 0
                if a_drop < DECEL_FRACTION or b_drop < DECEL_FRACTION:
                    continue

                if a.flagged and b.flagged:
                    continue
                a.flagged = b.flagged = True

                # Confidence from how emphatic the signature is, capped:
                # geometry alone should never claim near-certainty.
                conf = min(0.94, 0.45 + overlap * 1.2
                           + (a_drop + b_drop - 1.4) * 0.5)
                out.append({
                    "kind": "accident",
                    "score": round(max(0.5, conf), 3),
                    "detail": (
                        f"Two vehicles made contact (overlap {overlap:.0%}) and both "
                        f"lost {min(a_drop, b_drop):.0%}+ of their speed within a second."
                    ),
                    "evidence": {
                        "signature": "collision",
                        "track_a": a_id, "track_b": b_id,
                        "overlap_iou": round(overlap, 3),
                        "speed_before_px_s": [round(a_before, 1), round(b_before, 1)],
                        "speed_after_px_s": [round(a_now, 1), round(b_now, 1)],
                        "deceleration": [f"{a_drop:.0%}", f"{b_drop:.0%}"],
                    },
                    "box": _union_box(boxes[a_id], boxes[b_id]),
                })
        return out

    # --- signature 2: a vehicle stopped where traffic should flow ---
    def _stopped_in_lane(self, t: float) -> list[dict]:
        out = []
        for tid, tr in self.tracks.items():
            if tr.flagged or len(tr.history) < MIN_HISTORY:
                continue
            moving_before = tr.prior_speed(back=STOPPED_S + 1.0) > 15
            if tr.speed() < STOPPED_SPEED_PX:
                if tr.stopped_since is None:
                    tr.stopped_since = t
                elif (t - tr.stopped_since >= STOPPED_S) and moving_before:
                    tr.flagged = True
                    out.append({
                        "kind": "stopped_vehicle",
                        "score": 0.62,
                        "detail": (
                            f"A moving vehicle stopped and has not moved for "
                            f"{t - tr.stopped_since:.0f}s — consistent with a "
                            f"breakdown or a collision aftermath."
                        ),
                        "evidence": {
                            "signature": "stopped_in_lane",
                            "track": tid,
                            "stopped_for_s": round(t - tr.stopped_since, 1),
                            "speed_before_px_s": round(tr.prior_speed(), 1),
                        },
                        "box": tr.box,
                    })
            else:
                tr.stopped_since = None
        return out


def _union_box(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
