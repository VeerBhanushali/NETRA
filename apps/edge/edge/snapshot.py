"""Single-frame analysis for handheld and mobile cameras.

The fixed cameras in multicam.py get many frames of the same vehicle and
settle a plate by temporal voting. A phone held up at a checkpoint gets
one frame at a time from a moving hand, so there is no track to
accumulate across.

That difference is not cosmetic, and this module refuses to paper over
it. A single frame cannot satisfy the evidence rule the rest of NETRA is
built on (MIN_FRAMES = 3, FULL_EVIDENCE_FRAMES = 8), so a plate read here
is never published as a confirmed sighting. It is evidence, capped and
routed to a human. The same restraint the doc asks for in §12 — expose
observable classes and scores, never assert a conclusion.

Models are loaded lazily and shared, because this runs inside the API
process alongside the fixed-camera workers and must not cost anything
until a phone actually asks for it.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from .plate_norm import is_valid, repair
from .voting import frame_quality

REPO_ROOT = Path(__file__).resolve().parents[3]

# A one-frame read can never reach the auto-accept threshold (0.90). This
# ceiling is what keeps a handheld camera out of the confirmed-sightings
# table and inside the review queue where it belongs.
SINGLE_FRAME_CEILING = float(os.getenv("NETRA_SNAPSHOT_CEILING", "0.80"))
MIN_PLATE_CONF = float(os.getenv("NETRA_SNAPSHOT_MIN_CONF", "0.35"))

# The shortest real Indian plate body is 8 characters (KL07BA5252) and the
# shortest anywhere we support is 7 (UK: AB12CDE). Anything shorter is a
# fragment — a door number, a sticker, half a plate. Testing turned up
# exactly this: OCR returned "631" at 0.9999 confidence off a partly
# occluded car and it would have entered the review queue at the ceiling.
# High OCR confidence on a fragment is confidently wrong, not nearly right.
MIN_PLATE_LEN = int(os.getenv("NETRA_SNAPSHOT_MIN_LEN", "7"))

# COCO classes we can honestly report. YOLOv8n is trained on COCO, which
# contains knife, scissors and baseball bat but NO firearm class — so this
# system cannot claim to detect guns, and does not pretend to.
PERSON_CLASS = 0
THREAT_CLASSES = {
    34: "baseball bat",
    43: "knife",
    76: "scissors",
}
VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

# Two people whose boxes overlap this much, this consistently, are in
# contact rather than merely walking past each other. It is reported as
# an observation — "two people in sustained close contact" — and never as
# "fighting". Distinguishing a fight from a hug, a handshake or a crowd
# needs a trained action model; the accident detector already taught us
# what happens when a heuristic is asked to make that call.
#
# 0.20 because person boxes are tall and narrow: two people standing
# shoulder to shoulder overlap around 0.15-0.25, and people grappling
# overlap far more. The threshold is set to raise a frame for review, not
# to draw a line between a fight and a conversation — nothing here can
# draw that line.
CONTACT_IOU = float(os.getenv("NETRA_CONTACT_IOU", "0.20"))

_lock = threading.Lock()
_models: dict[str, object] = {}


def _load() -> dict:
    """Load the shared models once, on first use."""
    with _lock:
        if _models:
            return _models
        from ultralytics import YOLO
        import easyocr

        _models["objects"] = YOLO(os.getenv("NETRA_VEHICLE_MODEL", "yolov8n.pt"))
        plate_path = REPO_ROOT / "models" / "onnx" / "plate_yolo11n.pt"
        _models["plate"] = YOLO(str(plate_path)) if plate_path.is_file() else None
        _models["ocr"] = easyocr.Reader(["en"], gpu=False, verbose=False)
        print("[snapshot] mobile analysis models loaded"
              f" (plate detector: {'yes' if _models['plate'] else 'no'})")
        return _models


def _read_plate(crop) -> tuple[str, float]:
    import cv2

    h, w = crop.shape[:2]
    if w < 240:
        s = 240.0 / max(w, 1)
        crop = cv2.resize(crop, (int(w * s), max(1, int(h * s))),
                          interpolation=cv2.INTER_CUBIC)
    try:
        found = _models["ocr"].readtext(                     # type: ignore[union-attr]
            crop, allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            detail=1, paragraph=False)
    except Exception:                                        # noqa: BLE001
        return "", 0.0
    if not found:
        return "", 0.0
    found.sort(key=lambda f: f[0][0][0])
    text = "".join(ch for f in found for ch in str(f[1]).upper() if ch.isalnum())
    conf = sum(float(f[2]) for f in found) / len(found)
    return text, conf


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix, iy = max(0, min(ax2, bx2) - max(ax1, bx1)), max(0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    if not inter:
        return 0.0
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union else 0.0


def analyse(img, want: set[str]) -> dict:
    """Analyse one frame. `want` ⊆ {"plate", "threat", "objects"}.

    Faces stay in faces.py: they already have their own gallery and
    matching path, and duplicating them here would give two answers to
    the same question.
    """
    import cv2
    import numpy as np

    m = _load()
    out: dict = {"plates": [], "threat": None, "objects": []}

    model = m["objects"]
    res = model(img, verbose=False, conf=0.30)                # type: ignore[operator]
    boxes = res[0].boxes if res else []
    names = model.names                                       # type: ignore[union-attr]

    people: list[tuple] = []
    threats: list[dict] = []

    for b in boxes:
        cls = int(b.cls.item())
        conf = float(b.conf.item())
        x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].tolist())

        # "Anything" means anything: all 80 COCO classes are reported when
        # asked, not just the three that happen to be sharp. An operator
        # watching a live feed wants to see what the model sees.
        if "objects" in want:
            out["objects"].append({"class": names[cls], "confidence": round(conf, 3),
                                   "box": [x1, y1, x2, y2],
                                   "threat_class": cls in THREAT_CLASSES})

        if cls == PERSON_CLASS:
            people.append((x1, y1, x2, y2))
        elif cls in THREAT_CLASSES and "threat" in want:
            threats.append({"class": THREAT_CLASSES[cls], "confidence": round(conf, 3),
                            "box": [x1, y1, x2, y2]})
        elif cls in VEHICLE_CLASSES and "plate" in want:
            vehicle = img[max(0, y1):y2, max(0, x1):x2]
            if vehicle.size == 0:
                continue
            read = _plate_from_vehicle(vehicle, (x1, y1), VEHICLE_CLASSES[cls])
            if read:
                out["plates"].append(read)

    if "threat" in want:
        out["threat"] = threat_evidence(people, threats)

    return out


def _plate_from_vehicle(vehicle, origin: tuple[int, int], kind: str) -> dict | None:
    import cv2

    ox, oy = origin
    m = _models
    if m.get("plate") is not None:
        pres = m["plate"](vehicle, verbose=False, conf=0.25)  # type: ignore[operator]
        pb = pres[0].boxes if pres else []
        if not len(pb):
            return None
        best = max(pb, key=lambda b: float(b.conf.item()))
        px1, py1, px2, py2 = (int(v) for v in best.xyxy[0].tolist())
        pad = 2
        crop = vehicle[max(0, py1 - pad):py2 + pad, max(0, px1 - pad):px2 + pad]
        box = [ox + max(0, px1 - pad), oy + max(0, py1 - pad), ox + px2 + pad, oy + py2 + pad]
    else:
        vh, vw = vehicle.shape[:2]
        crop = vehicle[int(vh * 0.5):vh, int(vw * 0.1):int(vw * 0.9)]
        box = [ox, oy, ox + vw, oy + vh]

    if crop.size == 0 or crop.shape[1] < 30:
        return None

    text, conf = _read_plate(crop)
    if not text or conf < MIN_PLATE_CONF or len(text) < MIN_PLATE_LEN:
        return None

    plate, repaired = repair(text)
    ch, cw = crop.shape[:2]
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    quality = frame_quality(
        bbox_area_px=float(cw * ch),
        sharpness=float(cv2.Laplacian(grey, cv2.CV_64F).var()),
        aspect=cw / max(1.0, float(ch)),
        detector_conf=conf)

    # Confidence is the OCR score damped by frame quality, damped again if
    # the string is not a well-formed plate for the region, and then
    # capped. No temporal evidence exists, so this can never present as
    # certainty however sure the recogniser claims to be.
    valid = is_valid(plate)
    score = conf * (0.5 + 0.5 * quality) * (1.0 if valid else 0.6)
    score = min(SINGLE_FRAME_CEILING, score)

    return {"plate": plate, "raw_text": text, "confidence": round(score, 4),
            "ocr_confidence": round(conf, 4), "quality": round(quality, 3),
            "grammar_valid": valid, "repaired": repaired,
            "vehicle_type": kind,
            "box": box, "frames": 1, "decision": "review"}


def threat_evidence(people: list[tuple], objects: list[dict]) -> dict:
    """Assemble threat *evidence*, not a verdict.

    §12 of the requirements is explicit: do not encode 'knife = threat'.
    A knife in a kitchen is a kitchen. What the backend can honestly
    report is which observable classes were seen, whether an object was
    near a person's hands, and how those combine into a score that an
    operator then judges.
    """
    contributions: list[dict] = []
    score = 0.0

    for o in objects:
        contributions.append({"kind": "object", "detail": o["class"],
                              "confidence": o["confidence"]})
        score = max(score, 0.35 * o["confidence"])

    interactions = 0
    for o in objects:
        ox1, oy1, ox2, oy2 = o["box"]
        ocx, ocy = (ox1 + ox2) / 2, (oy1 + oy2) / 2
        for (px1, py1, px2, py2) in people:
            if px1 <= ocx <= px2 and py1 <= ocy <= py2:
                interactions += 1
                # Upper half of the person box is roughly torso-and-hands.
                near_hands = ocy < py1 + 0.6 * (py2 - py1)
                contributions.append({
                    "kind": "interaction",
                    "detail": f"{o['class']} within person bounds"
                              + (" at hand height" if near_hands else ""),
                    "confidence": o["confidence"]})
                score = max(score, (0.75 if near_hands else 0.55) * o["confidence"])
                break

    # Person-to-person contact. Two people grappling and two people
    # hugging produce the same boxes, so this is reported as contact and
    # scored low on its own — it raises the frame for a human to look at,
    # which is the whole job. Calling it "fighting" would be a claim the
    # model cannot support.
    contacts = 0
    for i in range(len(people)):
        for j in range(i + 1, len(people)):
            if iou(people[i], people[j]) >= CONTACT_IOU:
                contacts += 1
    if contacts:
        contributions.append({
            "kind": "interaction",
            "detail": f"{contacts} pair(s) of people in close contact — "
                      "could be a struggle, could be a conversation",
            "confidence": 0.5})
        score = max(score, 0.40)

    if people:
        contributions.append({"kind": "person", "detail": f"{len(people)} person track(s)",
                              "confidence": 1.0})

    # A single frame carries no temporal evidence, which §12 lists as its
    # own axis. Say so rather than quietly scoring as if it were there.
    if score:
        contributions.append({"kind": "temporal",
                              "detail": "single frame — no sustained sequence observed",
                              "confidence": 0.0})
        score *= 0.85

    return {
        "score": round(min(score, SINGLE_FRAME_CEILING), 3),
        "objects": objects,
        "people": len(people),
        "interactions": interactions,
        "contacts": contacts,
        "contributions": contributions,
        "decision": "review" if score >= 0.30 else "none",
        "note": "Observable classes and proximity only. COCO has no firearm "
                "class, so firearms are out of scope for this detector.",
    }
