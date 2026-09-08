"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { PageHeader } from "@/components/Shell";
import { EmptyState, PanelHeader } from "@/components/ui";
import { useOperatorCamera } from "@/components/OperatorCamera";

/** Full view of the operator's own camera.
 *
 *  The wall tile answers "is something there". This page answers "what
 *  exactly, and should I believe it" — which for a face means showing the
 *  runner-up score, not only the winner. A 0.66 match is convincing when
 *  the second-best is 0.21 and worthless when the second-best is 0.64.
 */
export default function OperatorCameraPage() {
  const {
    videoRef, canvasRef, ready, error, start, stop, running, setRunning,
    faces, plates, threat, objects, sent, busy, apiError, boxes, scale: s,
  } = useOperatorCamera("cam-live");

  const shellRef = useRef<HTMLDivElement>(null);
  const [fullscreen, setFullscreen] = useState(false);

  const toggleFullscreen = async () => {
    const el = shellRef.current;
    if (!el) return;
    if (!document.fullscreenElement) {
      await el.requestFullscreen().catch(() => {});
    } else {
      await document.exitFullscreen().catch(() => {});
    }
  };

  useEffect(() => {
    const onChange = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const counts = objects.reduce<Record<string, number>>((acc, o) => {
    acc[o.class] = (acc[o.class] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <>
      <PageHeader
        title="Operator camera"
        description="cam-live · faces, plates, objects and threat evidence on one feed"
      >
        <Link href="/live" className="btn">All cameras</Link>
        {!ready ? (
          <button className="btn btn-primary" onClick={start}>Start camera</button>
        ) : (
          <>
            <button className="btn" onClick={() => { setRunning(false); stop(); }}>
              Release camera
            </button>
            <button className="btn btn-primary" onClick={() => setRunning((r) => !r)}>
              {running ? "Stop scanning" : "Start scanning"}
            </button>
          </>
        )}
        <button className="btn" onClick={toggleFullscreen}>
          {fullscreen ? "Exit full screen" : "Full screen"}
        </button>
      </PageHeader>

      <div className="grid gap-6 p-6 xl:grid-cols-[1.7fr_1fr]">
        {/* ---- the feed ---- */}
        <section ref={shellRef} className="panel overflow-hidden bg-surface">
          <div className="flex items-center justify-between border-b border-hairline px-4 h-11">
            <div className="flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${running ? "dot-live" : ""}`}
                    style={{ background: running ? "var(--ok)" : "var(--ink-subtle)" }} />
              <span className="mono text-[12px] text-ink">cam-live</span>
              <span className="label-micro">
                {busy ? "scanning" : running ? "watching" : ready ? "idle" : "camera off"}
              </span>
            </div>
            <span className="label-micro">
              faint box = object seen · green = face · blue = plate · amber = threat
            </span>
          </div>

          <div className="relative flex items-center justify-center bg-[#0b0b0d]"
               style={{ minHeight: fullscreen ? "calc(100vh - 44px)" : 460 }}>
            <video ref={videoRef} playsInline muted
                   className="max-h-full max-w-full object-contain"
                   style={{ display: ready ? "block" : "none",
                            maxHeight: fullscreen ? "calc(100vh - 44px)" : 560 }} />
            <canvas ref={canvasRef} className="hidden" />

            {ready && boxes.map((b, i) => (
              <div key={i} className="pointer-events-none absolute"
                   style={{ left: (videoRef.current?.offsetLeft ?? 0) + b.box[0] * s,
                            top: (videoRef.current?.offsetTop ?? 0) + b.box[1] * s,
                            width: (b.box[2] - b.box[0]) * s,
                            height: (b.box[3] - b.box[1]) * s,
                            border: `${b.faint ? 1 : 2}px solid ${b.colour}`,
                            opacity: b.faint ? 0.5 : 1 }}>
                {b.label && (
                  <span className={`absolute -top-5 left-0 whitespace-nowrap px-1
                                    text-[10px] font-semibold ${b.mono ? "mono" : ""}`}
                        style={{ background: b.colour, color: "#fff",
                                 opacity: b.faint ? 0.7 : 1 }}>
                    {b.label}
                  </span>
                )}
              </div>
            ))}

            {!ready && (
              <div className="px-6 py-20 text-center">
                <div className="label-micro" style={{ color: "var(--ink-subtle)" }}>
                  Camera not started
                </div>
                <p className="mt-2 max-w-md text-[12px] text-ink-subtle">
                  This channel runs all four models on the same frames. Nothing is
                  recorded until you start scanning.
                </p>
                <button className="btn btn-primary mt-4" onClick={start}>
                  Start camera
                </button>
                {error && (
                  <p className="mt-3 text-[12px]" style={{ color: "var(--danger)" }}>
                    {error}
                  </p>
                )}
              </div>
            )}
          </div>
        </section>

        {/* ---- what it found ---- */}
        <div className="space-y-6">
          <section className="panel overflow-hidden">
            <PanelHeader title="Faces">
              <span className="label-micro">
                {faces.length === 0 ? "none" : `${faces.length} in frame`}
              </span>
            </PanelHeader>
            {faces.length === 0 ? (
              <EmptyState title="No face in frame" />
            ) : (
              <div className="divide-y divide-hairline">
                {faces.map((f, i) => (
                  <div key={i} className="px-4 py-3">
                    <div className="flex items-baseline justify-between">
                      <span className="text-[13px] font-semibold text-ink">
                        {f.match ? f.match.name : "Unknown person"}
                      </span>
                      <span className="mono text-[11px]"
                            style={{ color: f.match
                              ? (f.match.decision === "confirm"
                                  ? "var(--danger)" : "var(--warn)")
                              : "var(--ink-subtle)" }}>
                        {f.match ? `${(f.match.score * 100).toFixed(1)}% ${f.match.decision}`
                                 : "no gallery match"}
                      </span>
                    </div>

                    {f.match && (
                      <div className="mt-1 text-[11px] text-ink-subtle">
                        {f.match.category?.replace(/_/g, " ")}
                        {f.match.case_ref ? ` · case ${f.match.case_ref}` : ""}
                        {f.match.expires_at ? ` · expires ${f.match.expires_at.slice(0, 10)}` : ""}
                      </div>
                    )}

                    {/* Image quality first: a match off a 40 px blurred face
                        is not the same claim as one off a sharp 200 px face. */}
                    <dl className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
                      <div>
                        <dt className="label-micro">Size</dt>
                        <dd className="mono text-ink">{f.width_px}×{f.height_px}px</dd>
                      </div>
                      <div>
                        <dt className="label-micro">Quality</dt>
                        <dd className="mono" style={{ color: f.usable ? "var(--ink)" : "var(--warn)" }}>
                          {(f.quality * 100).toFixed(0)}%
                        </dd>
                      </div>
                      <div>
                        <dt className="label-micro">Detector</dt>
                        <dd className="mono text-ink">{(f.det_score * 100).toFixed(0)}%</dd>
                      </div>
                    </dl>

                    {!f.usable && (
                      <p className="mt-1 text-[11px]" style={{ color: "var(--warn)" }}>
                        Too small or too blurred to enrol against — move closer.
                      </p>
                    )}

                    {f.gallery_size === 0 ? (
                      <p className="mt-2 text-[11px] text-ink-subtle">
                        Nobody is enrolled yet. <Link href="/persons" className="link">
                        Enrol a person</Link> to match against.
                      </p>
                    ) : f.candidates?.length > 0 && (
                      <div className="mt-2">
                        <div className="label-micro">Closest enrolled</div>
                        {f.candidates.map((c: any, j: number) => (
                          <div key={`${c.person_id}-${j}`}
                               className="flex items-baseline justify-between py-0.5">
                            <span className="text-[11px] text-ink">{c.name}</span>
                            <span className="mono text-[11px] text-ink-subtle">
                              {(c.score * 100).toFixed(1)}%
                            </span>
                          </div>
                        ))}
                        <p className="mt-1 text-[11px] text-ink-subtle">
                          Confirm at 65% · review from 45% · {f.gallery_size} template
                          {f.gallery_size === 1 ? "" : "s"} enrolled
                        </p>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel overflow-hidden">
            <PanelHeader title="Threat evidence" />
            {!threat || threat.decision === "none" ? (
              <EmptyState title="No threat evidence"
                          hint="Objects and person-to-person contact are scored here" />
            ) : (
              <div className="px-4 py-3">
                <div className="flex items-baseline justify-between">
                  <span className="text-[13px] font-semibold text-ink">
                    Candidate — needs an operator
                  </span>
                  <span className="mono text-[12px]" style={{ color: "var(--warn)" }}>
                    {(threat.score * 100).toFixed(0)}%
                  </span>
                </div>
                <ul className="mt-2 space-y-1">
                  {threat.contributions.map((c: any, i: number) => (
                    <li key={i} className="text-[11px] text-ink">
                      <span className="label-micro mr-1">{c.kind}</span>{c.detail}
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-[11px] text-ink-subtle">{threat.note}</p>
              </div>
            )}
          </section>

          <section className="panel overflow-hidden">
            <PanelHeader title="Everything detected">
              <span className="label-micro">{objects.length} object
                {objects.length === 1 ? "" : "s"}</span>
            </PanelHeader>
            {objects.length === 0 ? (
              <EmptyState title="Nothing detected" />
            ) : (
              <div className="flex flex-wrap gap-1.5 p-4">
                {Object.entries(counts)
                  .sort((a, b) => b[1] - a[1])
                  .map(([cls, n]) => (
                  <span key={cls} className="px-2 py-0.5 text-[11px]"
                        style={{ background: "var(--surface-sunken)",
                                 border: "1px solid var(--border)" }}>
                    {cls}{n > 1 ? ` ×${n}` : ""}
                  </span>
                ))}
              </div>
            )}
          </section>

          {plates.length > 0 && (
            <section className="panel overflow-hidden">
              <PanelHeader title="Plates" />
              <div className="divide-y divide-hairline">
                {plates.map((p, i) => (
                  <div key={`${p.plate}-${i}`} className="flex items-baseline
                                                          justify-between px-4 py-2">
                    <span className="mono text-[14px] font-semibold text-ink">{p.plate}</span>
                    <span className="text-[11px] text-ink-subtle">
                      {(p.confidence * 100).toFixed(0)}% · 1 frame · review queue
                    </span>
                  </div>
                ))}
              </div>
            </section>
          )}

          <p className="text-[11px] text-ink-subtle">
            {sent} frame{sent === 1 ? "" : "s"} analysed.
            {apiError && (
              <span style={{ color: "var(--danger)" }}> · {apiError}</span>
            )}
          </p>
        </div>
      </div>
    </>
  );
}
