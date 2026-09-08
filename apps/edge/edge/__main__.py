"""Edge worker CLI.

    python -m edge --mode simulate --rate 2
    python -m edge --mode simulate --incident clone
    python -m edge --mode video --source datasets/demo_clips/traffic.mp4 --camera cam-01
    python -m edge --mode providers          # what will inference actually run on?
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(prog="edge", description="NETRA edge worker")
    ap.add_argument("--mode", default="simulate",
                    choices=["network", "simulate", "anpr", "video", "providers"])
    ap.add_argument("--seconds", type=float, default=None,
                    help="stop ANPR after N seconds of wall clock")
    ap.add_argument("--loop", action="store_true",
                    help="restart the video when it ends (continuous demo)")
    ap.add_argument("--rate", type=float, default=1.5,
                    help="simulated vehicle passes per second")
    ap.add_argument("--duration", type=float, default=None,
                    help="stop after N seconds (default: run until Ctrl-C)")
    ap.add_argument("--incident", choices=["clone", "speed"], default=None,
                    help="inject a scripted incident during the run")
    ap.add_argument("--source", action="append",
                    help="video file or RTSP URL; repeat for a playlist")
    ap.add_argument("--camera", default="cam-01", help="camera id to attribute to")
    ap.add_argument("--show", action="store_true", help="write an annotated debug video")
    ap.add_argument("--only", action="append",
                    help="restrict --mode network to these camera ids")
    args = ap.parse_args()

    if args.mode == "network":
        from .multicam import run_network
        return run_network(only=args.only)

    if args.mode == "simulate":
        from .simulate import run
        run(rate=args.rate, duration=args.duration, incident=args.incident)
        return 0

    if args.mode == "providers":
        from .runtime import describe_runtime
        print(describe_runtime())
        return 0

    if args.mode == "anpr":
        if not args.source:
            ap.error("--source is required for --mode anpr")
        from .anpr import run_anpr
        return run_anpr(args.source, args.camera,
                        max_seconds=args.seconds, loop=args.loop)

    if args.mode == "video":
        if not args.source:
            ap.error("--source is required for --mode video")
        from .pipeline import run_video
        return run_video(args.source[0], args.camera, debug_video=args.show)

    return 1


if __name__ == "__main__":
    sys.exit(main())
