"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useCamera } from "@/components/Camera";

/** The third channel: the operator's own camera, running all three models.
 *
 *  cam-01 reads plates and cam-02 watches people, each doing one job on
 *  recorded footage. This channel does all three at once — faces, plates
 *  and threat objects — because unlike a fixed camera it is being pointed
 *  by a person who knows what they are pointing it at.
 *
 *  It is also how the threat detector gets demonstrated honestly: hold a
 *  knife or a pair of scissors in frame and the evidence appears. No
 *  recorded CCTV clip we have contains one, and staging a fake was the
 *  wrong answer.
 */
type Box = { box: number[]; colour: string; label?: string; mono?: boolean;
             faint?: boolean };

export function useOperatorCamera(cameraId: string) {
  const { videoRef, canvasRef, ready, error, start, stop, capture } = useCamera();
  const inFlight = useRef(false);
  const [running, setRunning] = useState(false);
  const [faces, setFaces] = useState<any[]>([]);
  const [plates, setPlates] = useState<any[]>([]);
  const [threat, setThreat] = useState<any>(null);
  const [objects, setObjects] = useState<any[]>([]);
  const [sent, setSent] = useState(0);
  const [busy, setBusy] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  const scan = useCallback(async () => {
    // One request at a time. The three models take ~0.3 s on this
    // machine, well over the frame interval, so an unguarded loop would
    // queue requests faster than the server retires them.
    if (inFlight.current) return;
    const image = capture(960);
    if (!image) return;
    inFlight.current = true;
    setBusy(true);
    try {
      const res = await fetch("/api/v1/devices/frame", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          camera_id: cameraId, image, name: "Operator camera",
          analyse: "face,plate,threat,objects",
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      setFaces(d.faces ?? []);
      setPlates(d.plates ?? []);
      setThreat(d.threat ?? null);
      setObjects(d.objects ?? []);
      setSent((n) => n + 1);
      setApiError(d.analysis_error ?? null);
    } catch (e: any) {
      setApiError(String(e?.message ?? e));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }, [capture, cameraId]);

  useEffect(() => {
    if (!running || !ready) return;
    const id = setInterval(scan, 1200);
    return () => clearInterval(id);
  }, [running, ready, scan]);

  const shownW = videoRef.current?.clientWidth ?? 0;
  const capturedW = Math.min(960, videoRef.current?.videoWidth || 960);
  const s = shownW ? shownW / capturedW : 1;

  const boxes: Box[] = [
    // Everything the detector saw, drawn faintly underneath. A person or
    // a vehicle that also carries a face or a plate gets its specific box
    // drawn on top, so the specific reading always wins visually.
    ...objects
      .filter((o) => !o.threat_class)
      .map((o) => ({
        box: o.box, colour: "var(--border-strong)", faint: true,
        label: `${o.class} ${(o.confidence * 100).toFixed(0)}%`,
      })),
    ...faces.map((f) => ({
      box: f.box,
      colour: f.match
        ? (f.match.decision === "confirm" ? "var(--danger)" : "var(--warn)")
        : "var(--ok)",
      label: f.match ? `${f.match.name} ${(f.match.score * 100).toFixed(0)}%` : "face",
    })),
    ...plates.map((p) => ({
      box: p.box, colour: "var(--accent)", mono: true,
      label: `${p.plate} ${(p.confidence * 100).toFixed(0)}%`,
    })),
    ...((threat?.objects ?? []) as any[]).map((o) => ({
      box: o.box, colour: "var(--warn)",
      label: `${o.class} ${(o.confidence * 100).toFixed(0)}%`,
    })),
  ];

  return { videoRef, canvasRef, ready, error, start, stop, running, setRunning,
           faces, plates, threat, objects, sent, busy, apiError, boxes, scale: s };
}

/** The wall tile. The full-screen view lives at /live/operator and shares
 *  every bit of this logic through the hook above. */
export function OperatorCamera({ cameraId = "cam-live" }: { cameraId?: string }) {
  const {
    videoRef, canvasRef, ready, error, start, stop, running, setRunning,
    plates, threat, sent, busy, apiError, boxes, scale: s,
  } = useOperatorCamera(cameraId);

  return (
    <figure className="panel tile overflow-hidden"
            style={{ ["--tile-hue" as any]: running ? "var(--ok)" : "var(--border-strong)" }}>
      <div className="relative flex items-center justify-center bg-surface-sunken"
           style={{ aspectRatio: "3 / 2" }}>
        <video ref={videoRef} playsInline muted
               className="h-full w-full object-cover"
               style={{ display: ready ? "block" : "none" }} />
        <canvas ref={canvasRef} className="hidden" />

        {boxes.map((b, i) => (
          <div key={i} className="pointer-events-none absolute"
               style={{ left: b.box[0] * s, top: b.box[1] * s,
                        width: (b.box[2] - b.box[0]) * s,
                        height: (b.box[3] - b.box[1]) * s,
                        border: `${b.faint ? 1 : 2}px solid ${b.colour}`,
                        opacity: b.faint ? 0.55 : 1 }}>
            {!b.faint && (
              <span className={`absolute -top-5 left-0 whitespace-nowrap px-1
                                text-[10px] font-semibold ${b.mono ? "mono" : ""}`}
                    style={{ background: b.colour, color: "#fff" }}>
                {b.label}
              </span>
            )}
          </div>
        ))}

        {!ready && (
          <div className="px-4 text-center">
            <div className="label-micro">Operator camera</div>
            <p className="mt-1 mb-3 text-[11px] text-ink-subtle">
              Faces, plates and threat objects on one feed
            </p>
            <button className="btn btn-primary" onClick={start}>Start camera</button>
            {error && <p className="mt-2 text-[11px]" style={{ color: "var(--danger)" }}>{error}</p>}
          </div>
        )}

        {ready && (
          <span className="absolute left-2 top-2 flex items-center gap-1.5 px-1.5 py-0.5"
                style={{ background: "var(--scrim)", border: "1px solid var(--border)" }}>
            <span className={`h-1.5 w-1.5 rounded-full ${running ? "dot-live" : ""}`}
                  style={{ background: running ? "var(--ok)" : "var(--ink-subtle)" }} />
            <span className="mono text-[10px] text-ink">{cameraId}</span>
          </span>
        )}

        {ready && (
          <div className="absolute bottom-2 left-2 right-2 flex gap-2">
            <button className="btn btn-primary flex-1"
                    onClick={() => setRunning((r) => !r)}>
              {running ? "Stop scanning" : "Start scanning"}
            </button>
            <Link className="btn" href="/live/operator"
                  onClick={() => { setRunning(false); stop(); }}>
              Expand
            </Link>
            <button className="btn" onClick={() => { setRunning(false); stop(); }}>
              Release
            </button>
          </div>
        )}
      </div>

      <figcaption className="border-t border-hairline px-3 py-2">
        <div className="flex items-center justify-between">
          <span className="text-[12px] text-ink truncate">
            Operator camera · face + plate + threat
          </span>
          <span className="mono text-[10px] text-ink-subtle shrink-0">
            {busy ? "scanning" : `${sent} frames`}
          </span>
        </div>

        {plates.length > 0 && (
          <div className="mt-1 mono text-[11px] text-ink">
            {plates.map((p) => `${p.plate} ${(p.confidence * 100).toFixed(0)}%`).join(" · ")}
            <span className="ml-1 font-sans text-ink-subtle">→ review queue</span>
          </div>
        )}
        {threat && threat.decision !== "none" && (
          <div className="mt-1 text-[11px]" style={{ color: "var(--warn)" }}>
            threat evidence {(threat.score * 100).toFixed(0)}% ·{" "}
            {threat.contributions.map((c: any) => c.detail).join("; ")}
          </div>
        )}
        {apiError && (
          <div className="mt-1 text-[11px]" style={{ color: "var(--danger)" }}>{apiError}</div>
        )}
      </figcaption>
    </figure>
  );
}
