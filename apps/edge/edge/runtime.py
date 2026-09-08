"""Inference runtime selection.

This machine has an AMD Radeon RX 6500M (gfx1034) and no CUDA. That rules
out PyTorch-CUDA, TensorRT and DeepStream, and ROCm does not support this
GPU either. The workable path on Windows is ONNX Runtime with the
DirectML execution provider, falling back to CPU.

One trap worth stating plainly: installing both `onnxruntime` and
`onnxruntime-directml` leaves a conflicting install where DirectML
silently disappears. If `DmlExecutionProvider` is missing from the list
below, that is almost always why.

    pip uninstall -y onnxruntime onnxruntime-directml
    pip install onnxruntime-directml
"""
from __future__ import annotations

# Preference order. DirectML uses the AMD GPU; CPU is the safety net.
PREFERRED = ["DmlExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]


def available_providers() -> list[str]:
    try:
        import onnxruntime as ort
    except ImportError:
        return []
    return list(ort.get_available_providers())


def select_providers() -> list[str]:
    """Best available provider list, most preferred first."""
    have = available_providers()
    chosen = [p for p in PREFERRED if p in have]
    return chosen or ["CPUExecutionProvider"]


def make_session(model_path: str):
    """Create an ORT session on the best available device.

    Logs the provider actually in use — never assume the GPU was picked
    up, because a silent fall back to CPU is the difference between 25
    FPS and 3 FPS and it is easy to miss.
    """
    import onnxruntime as ort

    providers = select_providers()
    sess = ort.InferenceSession(model_path, providers=providers)
    actual = sess.get_providers()
    print(f"[runtime] {model_path}: requested {providers}, running on {actual}")
    if "DmlExecutionProvider" not in actual and "CUDAExecutionProvider" not in actual:
        print("[runtime] WARNING: running on CPU. Expect a few FPS, not real time.")
    return sess


def describe_runtime() -> str:
    have = available_providers()
    if not have:
        return ("onnxruntime is not installed.\n"
                "  pip install onnxruntime-directml   # AMD/Intel GPU on Windows\n"
                "  pip install onnxruntime            # CPU only\n"
                "Do NOT install both: they conflict and DirectML disappears.")
    lines = ["Available ONNX Runtime execution providers:"]
    lines += [f"  - {p}" for p in have]
    chosen = select_providers()[0]
    lines.append(f"\nWill use: {chosen}")
    if chosen == "CPUExecutionProvider":
        lines.append(
            "\nNo GPU provider found. On this AMD RX 6500M the fix is:\n"
            "  pip uninstall -y onnxruntime onnxruntime-directml\n"
            "  pip install onnxruntime-directml")
    return "\n".join(lines)
