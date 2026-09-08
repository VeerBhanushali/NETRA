"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Webcam capture.
 *
 *  Frames are drawn to a canvas and sent as JPEG data URLs. JPEG at 0.8
 *  rather than PNG: a 640x480 PNG is ~600 KB and would make a 2 fps scan
 *  loop saturate the connection for no accuracy gain — the detector sees
 *  no difference.
 */
export function useCamera() {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 },
                 facingMode: "user" },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setReady(true);
      setError(null);
    } catch (e: any) {
      // The browser only grants camera access on a secure origin;
      // localhost counts, a LAN IP does not. Say so plainly.
      setError(
        e?.name === "NotAllowedError"
          ? "Camera permission was denied. Allow it in the browser address bar."
          : e?.name === "NotFoundError"
          ? "No camera found on this device."
          : `${e?.name ?? "Error"}: ${e?.message ?? String(e)}`
      );
      setReady(false);
    }
  }, []);

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setReady(false);
  }, []);

  // Always release the device on unmount — a camera light left on after
  // the operator navigated away is both alarming and a real privacy fault.
  useEffect(() => stop, [stop]);

  const capture = useCallback((maxW = 960): string | null => {
    const v = videoRef.current;
    const c = canvasRef.current;
    if (!v || !c || !v.videoWidth) return null;
    const scale = Math.min(1, maxW / v.videoWidth);
    c.width = Math.round(v.videoWidth * scale);
    c.height = Math.round(v.videoHeight * scale);
    const ctx = c.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(v, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.8);
  }, []);

  return { videoRef, canvasRef, ready, error, start, stop, capture };
}

export function CameraView({
  videoRef, canvasRef, ready, boxes, height = 360,
}: {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  canvasRef: React.RefObject<HTMLCanvasElement | null>;
  ready: boolean;
  /** Detection overlays in captured-image pixel coordinates. */
  boxes?: { box: number[]; label?: string; tone?: "match" | "review" | "none" }[];
  height?: number;
}) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(1);

  // Boxes arrive in the coordinate space of the captured frame, which is
  // downscaled from the video element. Without this they land in the
  // wrong place — the classic overlay bug.
  useEffect(() => {
    const v = videoRef.current;
    if (!v || !wrapRef.current || !v.videoWidth) return;
    const shown = wrapRef.current.clientWidth;
    const captured = Math.min(960, v.videoWidth);
    setScale(shown / captured);
  }, [ready, boxes, videoRef]);

  return (
    <div ref={wrapRef} className="relative w-full overflow-hidden bg-[#0b0b0d]"
         style={{ height }}>
      <video ref={videoRef} playsInline muted
             className="h-full w-full object-contain" />
      <canvas ref={canvasRef} className="hidden" />

      {(boxes ?? []).map((b, i) => {
        const [x1, y1, x2, y2] = b.box;
        const colour = b.tone === "match" ? "var(--critical)"
          : b.tone === "review" ? "var(--high)" : "var(--ok)";
        return (
          <div key={i} className="pointer-events-none absolute"
               style={{ left: x1 * scale, top: y1 * scale,
                        width: (x2 - x1) * scale, height: (y2 - y1) * scale,
                        border: `2px solid ${colour}` }}>
            {b.label && (
              <span className="absolute -top-6 left-0 whitespace-nowrap px-1.5 py-0.5
                               text-[11px] font-semibold text-white"
                    style={{ background: colour }}>
                {b.label}
              </span>
            )}
          </div>
        );
      })}

      {!ready && (
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="label-micro" style={{ color: "var(--ink-subtle)" }}>
            camera off
          </span>
        </div>
      )}
    </div>
  );
}
