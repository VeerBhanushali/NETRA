"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/Shell";
import { CameraView, useCamera } from "@/components/Camera";
import { EmptyState, ErrorState, PanelHeader } from "@/components/ui";
import { timeOnly } from "@/lib/format";

interface ScanFace {
  box: number[]; quality: number; det_score: number;
  match: null | {
    person_id: string; name: string; category: string | null;
    case_ref: string | null; score: number; decision: "confirm" | "review";
    match_id?: number;
  };
}

export default function ScanPage() {
  const cam = useCamera();
  const [faces, setFaces] = useState<ScanFace[]>([]);
  const [gallery, setGallery] = useState(0);
  const [log, setLog] = useState<{ t: string; text: string; tone: string }[]>([]);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [rate, setRate] = useState(1);
  const inFlight = useRef(false);

  const scanOnce = useCallback(async () => {
    // Never queue a second request behind a slow one: the camera keeps
    // producing frames whether or not the server has replied, and a
    // backlog would show results from seconds ago as though they were now.
    if (inFlight.current) return;
    const img = cam.capture(800);
    if (!img) return;
    inFlight.current = true;
    try {
      const res = await fetch("/api/v1/faces/scan", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ image: img, camera_id: "cam-01", actor: "operator" }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail ?? `HTTP ${res.status}`);
      setFaces(d.results ?? []);
      setGallery(d.gallery_size ?? 0);
      setError(null);

      for (const f of d.results ?? []) {
        if (!f.match) continue;
        const line = `${f.match.name} — ${(f.match.score * 100).toFixed(1)}% (${f.match.decision})`;
        setLog((prev) => [{ t: new Date().toISOString(), text: line,
                            tone: f.match.decision }, ...prev].slice(0, 40));
      }
    } catch (e) {
      setError(e);
    } finally { inFlight.current = false; }
  }, [cam]);

  useEffect(() => {
    if (!scanning || !cam.ready) return;
    const id = setInterval(scanOnce, Math.max(400, 1000 / rate));
    return () => clearInterval(id);
  }, [scanning, cam.ready, rate, scanOnce]);

  const boxes = faces.map((f) => ({
    box: f.box,
    tone: f.match ? (f.match.decision === "confirm" ? "match" : "review") : "none",
    label: f.match
      ? `${f.match.name}  ${(f.match.score * 100).toFixed(0)}%`
      : "not on watchlist",
  })) as any;

  return (
    <>
      <PageHeader
        title="Face scan"
        description="Matches against the enrolled watchlist only. Non-matching faces are compared and discarded."
      >
        <span className="label-micro">{gallery} enrolled template(s)</span>
        <select className="input w-auto h-8 text-[13px]" value={rate}
                onChange={(e) => setRate(Number(e.target.value))}>
          <option value={0.5}>0.5 /s</option>
          <option value={1}>1 /s</option>
          <option value={2}>2 /s</option>
        </select>
        {!cam.ready ? (
          <button className="btn btn-primary" onClick={cam.start}>Start camera</button>
        ) : (
          <>
            <button className="btn btn-primary"
                    onClick={() => setScanning((s) => !s)}>
              {scanning ? "Stop scanning" : "Start scanning"}
            </button>
            <button className="btn" onClick={() => { setScanning(false); cam.stop(); }}>
              Stop camera
            </button>
          </>
        )}
      </PageHeader>

      <div className="grid gap-6 p-6 xl:grid-cols-[1.4fr_1fr]">
        <section className="panel overflow-hidden">
          <div className="flex items-center justify-between border-b border-hairline px-4 h-11">
            <div className="flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${scanning ? "dot-live" : ""}`}
                    style={{ background: scanning ? "var(--ok)" : "var(--ink-subtle)" }} />
              <span className="label-micro">
                {scanning ? "scanning" : cam.ready ? "camera on, idle" : "camera off"}
              </span>
            </div>
            <span className="label-micro">
              red = watchlist hit · amber = needs review · green = not enrolled
            </span>
          </div>

          <CameraView videoRef={cam.videoRef} canvasRef={cam.canvasRef}
                      ready={cam.ready} boxes={boxes} height={430} />

          {cam.error && <div className="p-3"><ErrorState error={{ message: cam.error }} compact /></div>}
          {error ? <div className="p-3"><ErrorState error={error} compact /></div> : null}

          <div className="border-t border-hairline px-4 py-3">
            <div className="flex flex-wrap gap-x-8 gap-y-2">
              <div>
                <div className="label-micro">Faces in frame</div>
                <div className="mono text-[15px] text-ink">{faces.length}</div>
              </div>
              <div>
                <div className="label-micro">Candidates</div>
                <div className="mono text-[15px]"
                     style={{ color: faces.some((f) => f.match) ? "var(--critical)" : "var(--ink)" }}>
                  {faces.filter((f) => f.match).length}
                </div>
              </div>
              <div>
                <div className="label-micro">Best quality</div>
                <div className="mono text-[15px] text-ink">
                  {faces.length ? Math.max(...faces.map((f) => f.quality)).toFixed(2) : "—"}
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="panel overflow-hidden">
          <PanelHeader title="Candidate log">
            <span className="label-micro">{log.length}</span>
          </PanelHeader>
          <div className="max-h-[520px] overflow-y-auto">
            {log.length === 0 ? (
              <EmptyState
                title="No candidates yet"
                hint="Enrol yourself on the Watchlist screen, then scan — the match appears here and on the frame."
              />
            ) : (
              <table className="w-full border-collapse">
                <thead className="table-head">
                  <tr><th className="num">Time</th><th>Candidate</th></tr>
                </thead>
                <tbody>
                  {log.map((l, i) => (
                    <tr key={i} className="table-row">
                      <td className="num text-[12px] text-ink-muted">{timeOnly(l.t)}</td>
                      <td className="text-[13px]"
                          style={{ color: l.tone === "confirm"
                            ? "var(--critical)" : "var(--high)" }}>
                        {l.text}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <div className="border-t border-hairline px-4 py-3">
            <p className="text-[11px] text-ink-subtle">
              A match is a <strong>candidate</strong>, never an identification. Every
              scan is written to the audit log, and only an operator confirming a
              candidate creates an alert.
            </p>
          </div>
        </section>
      </div>
    </>
  );
}
