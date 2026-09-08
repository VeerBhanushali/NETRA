"""Wanted-person recognition.

SCRFD detects faces, ArcFace turns each into a 512-d embedding, and the
embedding is matched against an enrolled watchlist. Both models are ONNX
and run on the GPU via DirectML.

The design decision that matters
--------------------------------
A single-frame face match at CCTV distance is unreliable — the same
problem we already solved for number plates. So this reuses the same
idea: accumulate embeddings across every frame of one *tracked* person,
weight each by measured face quality, and match the **averaged**
embedding rather than any single frame.

Averaging L2-normalised embeddings suppresses per-frame noise the way
per-character voting suppresses OCR noise. It is what lets us report a
calibrated match instead of a raw similarity number.

Privacy, enforced in code and not only in policy
------------------------------------------------
Faces that do not match the watchlist are never stored. Their embedding
exists for the duration of one comparison and is discarded. There is no
general face database here, and there is no code path that builds one.
"""
from __future__ import annotations

import os
import numpy as np

# Match bands. Cosine similarity on L2-normalised ArcFace embeddings.
# Start here, then CALIBRATE on a held-out set — the failure mode of a
# face system is accusing the wrong person, so the false-accept rate is
# the number that governs this threshold, not accuracy.
MATCH_CONFIRM = float(os.getenv("NETRA_FACE_MATCH", "0.65"))
MATCH_REVIEW = float(os.getenv("NETRA_FACE_REVIEW", "0.45"))
# A face smaller than this carries too little detail to identify anyone.
# ~80 px between the eyes is the usual working minimum; the box is wider.
MIN_FACE_PX = int(os.getenv("NETRA_FACE_MIN_PX", "60"))
# Frames required before a match is allowed to reach an operator.
MIN_FRAMES = int(os.getenv("NETRA_FACE_MIN_FRAMES", "5"))


def face_quality(box, det_score: float, frame_shape) -> float:
    """How much this view of a face deserves to influence the identity.

    Size dominates: a 40 px face is not a slightly worse 120 px face, it
    is a different problem. Detector confidence carries pose and blur
    implicitly, since SCRFD scores profile and motion-blurred faces low.
    """
    x1, y1, x2, y2 = box
    w, h = max(1, x2 - x1), max(1, y2 - y1)
    size = min(1.0, w / 110.0)
    # Very non-square boxes mean a bad crop or an extreme angle.
    aspect = max(0.0, 1.0 - abs((w / h) - 0.78) / 0.6)
    edge = 1.0
    fh, fw = frame_shape[:2]
    # A face clipped by the frame edge is partially missing.
    if x1 <= 2 or y1 <= 2 or x2 >= fw - 2 or y2 >= fh - 2:
        edge = 0.6
    q = size * aspect * edge * max(0.0, min(1.0, det_score))
    return round(q ** 0.5, 4)


class FaceEngine:
    """Shared SCRFD + ArcFace, on DirectML when available."""

    def __init__(self, prefer_gpu: bool = True) -> None:
        import onnxruntime as ort
        from insightface.app import FaceAnalysis

        avail = ort.get_available_providers()
        providers = (["DmlExecutionProvider", "CPUExecutionProvider"]
                     if prefer_gpu and "DmlExecutionProvider" in avail
                     else ["CPUExecutionProvider"])
        # buffalo_s: SCRFD-500M detector + a compact ArcFace. The large
        # pack is more accurate but ~4x the cost, and on CCTV-quality
        # faces the detector is the limiting factor, not the embedder.
        self.app = FaceAnalysis(
            name=os.getenv("NETRA_FACE_PACK", "buffalo_s"),
            providers=providers,
            allowed_modules=["detection", "recognition"],
        )
        self.app.prepare(ctx_id=0, det_size=(640, 640))
        self.providers = providers
        print(f"[faces] running on {providers[0]}")

    def detect(self, frame) -> list[dict]:
        """Every face in a frame, with its embedding.

        The embedding is returned to the caller and is expected to be
        discarded unless it matches the watchlist.
        """
        out = []
        for f in self.app.get(frame):
            x1, y1, x2, y2 = (int(v) for v in f.bbox)
            if (x2 - x1) < MIN_FACE_PX:
                continue
            emb = f.normed_embedding
            if emb is None:
                continue
            out.append({
                "box": (x1, y1, x2, y2),
                "embedding": np.asarray(emb, dtype=np.float32),
                "quality": face_quality((x1, y1, x2, y2), float(f.det_score),
                                        frame.shape),
                "det_score": float(f.det_score),
            })
        return out


class PersonAccumulator:
    """Accumulates one tracked person's face embeddings into an identity.

    The direct analogue of TrackAccumulator for plates.
    """

    def __init__(self, track_id: str, camera_id: str = "") -> None:
        self.track_id = track_id
        self.camera_id = camera_id
        self._sum: np.ndarray | None = None
        self._weight = 0.0
        self.frames = 0
        self.best_quality = 0.0
        self.best_crop = None

    def add(self, embedding: np.ndarray, quality: float, crop=None) -> None:
        # A hopeless view is noise, not weak evidence — the same lesson
        # the plate voter learned.
        if quality < 0.25:
            return
        self._sum = embedding * quality if self._sum is None else self._sum + embedding * quality
        self._weight += quality
        self.frames += 1
        if quality > self.best_quality:
            self.best_quality = quality
            self.best_crop = crop

    @property
    def embedding(self) -> np.ndarray | None:
        """Quality-weighted mean, re-normalised so cosine stays valid."""
        if self._sum is None or self._weight <= 0:
            return None
        v = self._sum / self._weight
        n = np.linalg.norm(v)
        return v / n if n > 0 else None

    def match(self, gallery: "Gallery") -> dict | None:
        """Compare the accumulated identity against the watchlist."""
        emb = self.embedding
        if emb is None or self.frames < MIN_FRAMES:
            return None
        person_id, score = gallery.best(emb)
        if person_id is None or score < MATCH_REVIEW:
            return None
        return {
            "person_id": person_id,
            "score": round(float(score), 4),
            "frames": self.frames,
            "quality": round(self.best_quality, 3),
            "decision": "confirm" if score >= MATCH_CONFIRM else "review",
        }


class Gallery:
    """The enrolled watchlist, held as a matrix for one-shot matching."""

    def __init__(self) -> None:
        self.ids: list[str] = []
        self._m: np.ndarray | None = None       # (N, 512), L2-normalised

    def add(self, person_id: str, embedding: np.ndarray) -> None:
        e = np.asarray(embedding, dtype=np.float32)
        n = np.linalg.norm(e)
        if n == 0:
            return
        e = e / n
        self.ids.append(person_id)
        self._m = e[None, :] if self._m is None else np.vstack([self._m, e])

    def __len__(self) -> int:
        return len(self.ids)

    def best(self, embedding: np.ndarray) -> tuple[str | None, float]:
        """Nearest enrolled identity by cosine similarity.

        One matrix-vector product regardless of gallery size — a watchlist
        of thousands costs the same as a watchlist of ten.
        """
        if self._m is None or len(self.ids) == 0:
            return None, 0.0
        sims = self._m @ embedding
        i = int(np.argmax(sims))
        return self.ids[i], float(sims[i])

    def ranked(self, embedding: np.ndarray, k: int = 3) -> list[tuple[str, float]]:
        """The top k enrolled identities, best first.

        An operator deciding whether a match is real needs to know what
        came second. A 0.66 top score is convincing when the runner-up is
        0.21 and worthless when the runner-up is 0.64 — the same reason
        the plate review screen shows its candidate list rather than only
        its best guess.

        Per-person scores are collapsed to that person's best template, so
        someone enrolled from five photos cannot crowd out the field.
        """
        if self._m is None or len(self.ids) == 0:
            return []
        sims = self._m @ embedding
        best: dict[str, float] = {}
        for pid, s in zip(self.ids, sims):
            f = float(s)
            if f > best.get(pid, -1.0):
                best[pid] = f
        return sorted(best.items(), key=lambda kv: kv[1], reverse=True)[:k]
