"""Face matching maths — the part that decides whether a person is flagged.

The detector and embedder are third-party ONNX models verified separately
(they load on DirectML and run at ~55 ms/frame). What is ours, and what
these tests cover, is the accumulate-then-match policy: quality
weighting, the evidence floor, and the decision bands.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edge.faces import (Gallery, PersonAccumulator, face_quality,   # noqa: E402
                        MATCH_CONFIRM, MATCH_REVIEW, MIN_FRAMES)


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def noisy(base, sigma, rng):
    return unit(base + rng.normal(0, sigma, base.shape).astype(np.float32))


def test_gallery_finds_nearest_identity():
    rng = np.random.default_rng(7)
    a, b = unit(rng.normal(size=512)), unit(rng.normal(size=512))
    g = Gallery()
    g.add("p-alice", a)
    g.add("p-bob", b)
    assert g.best(a)[0] == "p-alice"
    assert g.best(b)[0] == "p-bob"
    assert len(g) == 2


def test_empty_gallery_matches_nobody():
    """With nobody enrolled, no face can ever be identified."""
    pid, score = Gallery().best(unit(np.ones(512)))
    assert pid is None and score == 0.0


def test_averaging_beats_a_single_noisy_frame():
    """The same idea as plate voting: many weak looks beat one.

    Each frame is a badly degraded view. Individually they match poorly;
    the quality-weighted mean recovers the identity.
    """
    rng = np.random.default_rng(11)
    true = unit(rng.normal(size=512))
    g = Gallery(); g.add("p-target", true)

    frames = [noisy(true, 0.85, rng) for _ in range(12)]
    single = max(g.best(f)[1] for f in frames)

    acc = PersonAccumulator("cam-01:1")
    for f in frames:
        acc.add(f, quality=0.7)
    fused = g.best(acc.embedding)[1]

    assert fused > single, f"fusion {fused:.3f} should beat best single {single:.3f}"


def test_quality_floor_rejects_hopeless_views():
    """A view too poor to inform identity must not vote at all."""
    rng = np.random.default_rng(3)
    acc = PersonAccumulator("cam-01:2")
    for _ in range(5):
        acc.add(unit(rng.normal(size=512)), quality=0.10)
    assert acc.frames == 0
    assert acc.embedding is None


def test_too_few_frames_never_matches():
    """Below the evidence floor there is no decision, however similar."""
    rng = np.random.default_rng(5)
    true = unit(rng.normal(size=512))
    g = Gallery(); g.add("p-target", true)
    acc = PersonAccumulator("cam-01:3")
    for _ in range(MIN_FRAMES - 1):
        acc.add(true, quality=0.9)          # perfect, but not enough of them
    assert acc.match(g) is None


def test_decision_bands():
    rng = np.random.default_rng(13)
    true = unit(rng.normal(size=512))
    g = Gallery(); g.add("p-target", true)

    strong = PersonAccumulator("cam-01:4")
    for _ in range(MIN_FRAMES + 3):
        strong.add(true, quality=0.9)
    m = strong.match(g)
    assert m and m["decision"] == "confirm" and m["score"] >= MATCH_CONFIRM

    # A different person must not be returned as a candidate at all.
    other = PersonAccumulator("cam-01:5")
    stranger = unit(rng.normal(size=512))
    for _ in range(MIN_FRAMES + 3):
        other.add(stranger, quality=0.9)
    assert other.match(g) is None, "an unenrolled face must not match"


def test_face_quality_penalises_small_and_clipped():
    frame = (720, 1280, 3)
    big = face_quality((400, 200, 520, 350), 0.95, frame)
    small = face_quality((400, 200, 440, 250), 0.95, frame)
    clipped = face_quality((0, 200, 120, 350), 0.95, frame)
    assert big > small, "a larger face carries more identity information"
    assert big > clipped, "a face cut by the frame edge is partially missing"
