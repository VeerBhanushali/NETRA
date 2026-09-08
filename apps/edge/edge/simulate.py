"""Live camera simulator.

Generates a realistic stream of vehicle passes and pushes them through
the *real* voting engine and the *real* ingest API — only the pixels are
synthetic. It exists for two reasons:

  1. The system can be developed and demonstrated before a single model
     weight is downloaded or a camera is installed.
  2. It is the demo's fallback. If live inference misbehaves on stage,
     this produces the same dashboard behaviour on demand.

    python -m edge --mode simulate --rate 2
    python -m edge --mode simulate --incident clone
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timezone

from .plate_norm import STATE_CODES
from .publisher import Publisher
from .voting import PlateRead, TrackAccumulator, frame_quality

RNG = random.Random()
SERIES = ["AA", "AB", "AC", "BK", "CD", "DE", "DK", "GH", "JK", "MN"]
COLORS = ["white", "silver", "black", "red", "blue", "grey"]
VTYPES = ["car", "car", "car", "motorcycle", "truck", "bus"]
CONFUSIONS = {"0": "O", "O": "0", "1": "I", "8": "B", "5": "S", "2": "Z"}

CAMERAS = [f"cam-{i:02d}" for i in range(1, 11)]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def random_plate() -> str:
    return (f"{RNG.choice(sorted(STATE_CODES))}{RNG.randint(1, 68):02d}"
            f"{RNG.choice(SERIES)}{RNG.randint(1000, 9999)}")


def simulate_pass(plate: str, camera_id: str, frames: int = 22,
                  error_rate: float = 0.10) -> tuple[TrackAccumulator, str]:
    """Simulate one vehicle crossing one camera's field of view.

    Frame quality follows a realistic arc: the plate is small and blurry
    at the edges of the frame and sharpest as it passes the camera.
    """
    track_id = f"{camera_id}:{RNG.randint(1000, 9999)}"
    acc = TrackAccumulator(track_id, camera_id)

    for i in range(frames):
        # Bell-shaped quality across the pass.
        centrality = 1.0 - abs((i / max(1, frames - 1)) - 0.5) * 2
        area = 800 + 5200 * centrality
        sharp = 20 + 200 * centrality
        aspect = 2.0 + RNG.uniform(-0.35, 0.35)
        det_conf = 0.55 + 0.42 * centrality
        q = frame_quality(area, sharp, aspect, det_conf)

        # Worse frames make more character errors.
        rate = error_rate * (1.6 - centrality)
        chars, confs = [], []
        for ch in plate:
            if RNG.random() < rate:
                chars.append(CONFUSIONS.get(ch, RNG.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789")))
                confs.append(RNG.uniform(0.30, 0.62))
            else:
                chars.append(ch)
                confs.append(RNG.uniform(0.82, 0.99))
        acc.add(PlateRead("".join(chars), confs, quality=q, timestamp=now_iso()))

        if acc.stable and i >= 8:
            break                      # early exit: consensus already settled

    return acc, track_id


def to_payload(acc: TrackAccumulator, camera_id: str, track_id: str,
               color: str, vtype: str) -> dict | None:
    """Turn a settled vote into the ingest payload.

    Reads below the review floor are dropped here, at the edge — there is
    no value in shipping noise to the server.
    """
    r = acc.consensus()
    if r.decision == "discard":
        return None
    seen = now_iso()
    return {
        "plate": r.plate,
        "camera_id": camera_id,
        "seen_at": seen,
        "confidence": r.confidence,
        "track_id": track_id,
        "frame_count": r.frame_count,
        "color": color,
        "vehicle_type": vtype,
        "source": "sim",
        "dedupe_key": f"{track_id}:{seen}",
        # The per-frame evidence behind the vote, capped so a batch stays small.
        "reads": [
            {"raw_text": rd.text,
             "confidence": round(sum(rd.char_confidences) / len(rd.char_confidences), 3)
                           if rd.char_confidences else 0.5,
             "quality": rd.quality,
             "frame_ts": rd.timestamp or seen}
            for rd in acc.reads[:12]
        ],
    }


def run(rate: float = 1.5, duration: float | None = None,
        incident: str | None = None) -> None:
    """Stream simulated traffic into the API until interrupted."""
    pub = Publisher(batch_size=4)
    fleet = [(random_plate(), RNG.choice(COLORS), RNG.choice(VTYPES)) for _ in range(60)]
    started = time.time()
    n = 0

    print(f"[sim] publishing ~{rate}/s to the ingest API. Ctrl-C to stop.")
    if incident:
        print(f"[sim] will inject a '{incident}' incident shortly")

    # Last time each plate was published. Real vehicles cannot reappear at
    # a distant camera seconds later, and emitting them that way would
    # manufacture cloned-plate alerts — burying the real one in noise and
    # making the false-positive claim indefensible.
    last_emit: dict[str, float] = {}
    REVISIT_COOLDOWN_S = 900.0

    try:
        while duration is None or (time.time() - started) < duration:
            plate, color, vtype = RNG.choice(fleet)
            if time.time() - last_emit.get(plate, -1e9) < REVISIT_COOLDOWN_S:
                time.sleep(max(0.0, 1.0 / rate))
                continue
            last_emit[plate] = time.time()
            camera = RNG.choice(CAMERAS)
            acc, track_id = simulate_pass(plate, camera)
            payload = to_payload(acc, camera, track_id, color, vtype)
            if payload:
                pub.add(payload)
                n += 1
                r = acc.consensus()
                flag = "" if r.decision == "auto_accept" else "  -> REVIEW"
                print(f"  {payload['seen_at']}  {payload['plate']:<12} {camera}  "
                      f"conf {r.confidence:.3f}  frames {r.frame_count}{flag}")

            # Inject the scripted incident after a short warm-up so the
            # dashboard already has context when it fires.
            if incident and n == 12:
                _inject(pub, incident)
                incident = None

            time.sleep(max(0.0, 1.0 / rate))
    except KeyboardInterrupt:
        print("\n[sim] stopping")
    finally:
        pub.drain()
        print(f"[sim] published {pub.sent} sightings")


def _inject(pub: Publisher, kind: str) -> None:
    """Fire a specific alert type on cue, for a rehearsed demo beat."""
    plate = random_plate()
    if kind == "clone":
        # Same plate, two cameras 15 km apart, 60 seconds apart.
        for cam in ("cam-02", "cam-09"):
            acc, tid = simulate_pass(plate, cam, frames=26, error_rate=0.02)
            p = to_payload(acc, cam, tid, "white", "car")
            if p:
                p["confidence"] = max(p["confidence"], 0.97)
                pub.add(p)
        print(f"  ** injected CLONED PLATE for {plate}")
    elif kind == "speed":
        for cam in ("cam-08", "cam-09"):
            acc, tid = simulate_pass(plate, cam, frames=26, error_rate=0.02)
            p = to_payload(acc, cam, tid, "red", "car")
            if p:
                pub.add(p)
        print(f"  ** injected SPEEDING for {plate}")
    pub.flush()
