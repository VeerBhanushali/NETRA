"""Configuration, model registry and feature flags.

SIH26127 §6 lists ModelVersion / DetectionThreshold as core entities and
§8 requires AI services to be switchable by feature flag. Exposing the
thresholds read-only also matters for a different reason: an operator
looking at a 0.88 confidence needs to know what the accept bar is to
interpret it.
"""
from __future__ import annotations

from fastapi import APIRouter

from .. import config, registry

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/models")
def model_registry() -> dict:
    """Which model produced which kind of observation, and where it runs."""
    try:
        import onnxruntime as ort
        providers = ort.get_available_providers()
    except ImportError:
        providers = []
    gpu = "DmlExecutionProvider" in providers

    return {
        "execution_providers": providers,
        "gpu_available": gpu,
        # Measured on this machine, not vendor claims.
        "benchmarks": {
            "yolov8n_cpu_ms": 59.4, "yolov8n_directml_ms": 18.9,
            "plate_yolo11n_cpu_ms": 54.9, "plate_yolo11n_directml_ms": 11.1,
        },
        "models": [
            {"role": "vehicle_detection", "name": "yolov8n", "version": "8.4.142",
             "framework": "ultralytics", "device": "dml" if gpu else "cpu"},
            {"role": "plate_detection", "name": "plate_yolo11n", "version": "1.0.0",
             "framework": "ultralytics", "device": "dml" if gpu else "cpu"},
            {"role": "plate_ocr", "name": "easyocr", "version": "1.7.2",
             "framework": "pytorch", "device": "cpu",
             "note": "PyTorch, not ONNX — does not move to GPU until exported"},
            {"role": "tracking", "name": "bytetrack", "version": "1.0",
             "framework": "ultralytics", "device": "cpu"},
            {"role": "face_recognition", "name": "insightface/buffalo_s",
             "version": "2.0", "framework": "onnx",
             "device": "dml" if gpu else "cpu", "enabled": False},
        ],
    }


@router.get("/thresholds")
def thresholds() -> dict:
    """Every decision boundary the pipeline applies, in one place."""
    return {
        "anpr": {
            "auto_accept": config.AUTO_ACCEPT_CONFIDENCE,
            "review_min": config.REVIEW_CONFIDENCE,
            "note": "At or above auto_accept a plate is published as fact; "
                    "between the two a human decides; below, discarded.",
        },
        "cloned_plate": {
            "max_plausible_kmh": config.CLONE_SPEED_KMH,
            "min_distance_m": config.CLONE_MIN_DISTANCE_M,
            "clock_skew_s": config.CLOCK_SKEW_S,
            "min_read_confidence": config.CLONE_MIN_CONFIDENCE,
            "road_circuity_factor": config.CIRCUITY_FACTOR,
        },
        "speeding": {
            "limit_kmh": config.SPEED_LIMIT_KMH,
            "alert_margin_kmh": config.SPEED_ALERT_MARGIN_KMH,
        },
        "loitering": {
            "window_min": config.LOITER_WINDOW_MIN,
            "min_passes": config.LOITER_MIN_SIGHTINGS,
            "min_dwell_s": config.LOITER_MIN_DWELL_S,
        },
    }


@router.get("/features")
def feature_flags() -> dict:
    """What is switched on right now.

    `implemented` and `enabled` are separate on purpose: several modules
    exist as working code but are deliberately off, and a status endpoint
    that conflated the two would misrepresent the system.
    """
    import os

    def flag(env: str, default: str = "false") -> bool:
        return os.getenv(env, default).lower() == "true"

    return {
        "anpr":              {"implemented": True,  "enabled": True},
        "cross_camera_rules": {"implemented": True, "enabled": True},
        "human_review":      {"implemented": True,  "enabled": True},
        "face_recognition":  {"implemented": True,  "enabled": flag("NETRA_ENABLE_FACES"),
                              "note": "engine written; requires an authorised watchlist"},
        "accident_detection": {"implemented": True, "enabled": flag("NETRA_ENABLE_ACCIDENT"),
                               "note": "trajectory heuristic benchmarked and found "
                                       "insufficient — needs a trained model"},
        "threat_evidence":   {"implemented": True, "enabled": True,
                              "note": "observable classes (knife, scissors, bat) plus "
                                      "person-object interaction, scored as evidence "
                                      "for an operator — never asserted as a threat"},
        "weapon_detection":  {"implemented": False, "enabled": False,
                              "note": "COCO has no firearm class; would need a "
                                      "weapons dataset and a training run"},
        "mobile_camera":     {"implemented": True, "enabled": True,
                              "note": "phone as camera; single-frame reads are capped "
                                      "and routed to review, never published"},
        "owner_lookup":      {"implemented": True,
                              "enabled": registry.available()["configured"],
                              "note": registry.available()["note"]},
        "vehicle_reid":      {"implemented": False, "enabled": False},
        "cloud_ai":          {"implemented": True,  "enabled": config.AI_ENABLED},
    }
