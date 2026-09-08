"use client";

import { use, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { PageHeader } from "@/components/Shell";
import { Confidence, EmptyState, ErrorState, PanelHeader, Plate, SeverityPill }
  from "@/components/ui";
import { api, type Alert, type Camera, type Sighting } from "@/lib/api";
import { ALERT_LABEL, ago, dateTime, timeOnly } from "@/lib/format";
import { useLive } from "@/lib/useLive";

/** Full view of one camera.
 *
 *  The frame is drawn by the edge worker with the plate box and the string
 *  it currently believes, so what an operator sees here is exactly what
 *  the model saw — not a prettier reconstruction of it.
 */
export default function CameraViewPage({
  params,
}: { params: Promise<{ camera: string }> }) {
  const { camera: cameraId } = use(params);

  const [camera, setCamera] = useState<Camera | null>(null);
  const [sightings, setSightings] = useState<Sighting[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [src, setSrc] = useState<string | null>(null);
  const [alive, setAlive] = useState(false);
  const [fps, setFps] = useState(3);
  const [paused, setPaused] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [freshId, setFreshId] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const shellRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.cameras()
      .then((r) => {
        setCamera(r.results.find((c) => c.id === cameraId) ?? null);
        setLoadError(null);
      })
      .catch(setLoadError);
    fetch(`/api/sightings?limit=50&camera_id=${encodeURIComponent(cameraId)}`)
      .then((r) => r.json())
      .then((d) => setSightings(d.results ?? []))
      .catch(() => {});
    api.alerts({ limit: 20 })
      .then((r) => setAlerts(r.results.filter((a) => a.camera_id === cameraId)))
      .catch(() => {});
  }, [cameraId]);

  // Poll the annotated frame this camera's worker is writing, backing off
  // when there is nothing there so a dead camera cannot flood the network
  // tab with failed requests.
  useEffect(() => {
    if (paused) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    let misses = 0;

    const schedule = () => {
      if (stop) return;
      const base = Math.max(200, 1000 / fps);
      timer = setTimeout(tick, misses === 0 ? base : Math.min(15000, base * 2 ** misses));
    };
    const tick = () => {
      if (stop) return;
      const url = `/evidence/live/${cameraId}.jpg?t=${Date.now()}`;
      const img = new Image();
      img.onload = () => { if (!stop) { misses = 0; setSrc(url); setAlive(true); schedule(); } };
      img.onerror = () => { if (!stop) { misses += 1; setAlive(false); schedule(); } };
      img.src = url;
    };
    tick();
    return () => { stop = true; clearTimeout(timer); };
  }, [cameraId, fps, paused]);

  useLive((e) => {
    if (e.event === "sighting.created" && e.data?.camera_id === cameraId) {
      const s = e.data as Sighting;
      setSightings((prev) => [s, ...prev].slice(0, 50));
      setFreshId(s.id);
      setTimeout(() => setFreshId(null), 1400);
    }
    if (e.event === "alert.created" && e.data?.camera_id === cameraId) {
      setAlerts((prev) => [e.data as Alert, ...prev].slice(0, 20));
    }
  });

  const toggleFullscreen = async () => {
    const el = shellRef.current;
    if (!el) return;
    if (!document.fullscreenElement) {
      await el.requestFullscreen().catch(() => {});
      setFullscreen(true);
    } else {
      await document.exitFullscreen().catch(() => {});
      setFullscreen(false);
    }
  };

  useEffect(() => {
    const onChange = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  return (
    <>
      <PageHeader
        title={camera?.name ?? cameraId}
        description={camera
          ? `${cameraId} · ${camera.road_name ?? "—"}${camera.zone_name ? ` · ${camera.zone_name}` : ""}`
          : cameraId}
      >
        <Link href="/live" className="btn">All cameras</Link>
        <select className="input w-auto h-8 text-[13px]" value={fps}
                onChange={(e) => setFps(Number(e.target.value))}
                aria-label="Refresh rate">
          <option value={1}>1 fps</option>
          <option value={2}>2 fps</option>
          <option value={3}>3 fps</option>
          <option value={5}>5 fps</option>
        </select>
        <button className="btn" onClick={() => setPaused((p) => !p)}>
          {paused ? "Resume" : "Pause"}
        </button>
        <button className="btn btn-primary" onClick={toggleFullscreen}>
          {fullscreen ? "Exit full screen" : "Full screen"}
        </button>
      </PageHeader>

      {loadError ? (
        <div className="px-6 pt-6">
          <div className="panel"><ErrorState error={loadError} compact /></div>
        </div>
      ) : null}
      <div className="grid gap-6 p-6 xl:grid-cols-[1.7fr_1fr]">
        {/* ---- the feed ---- */}
        <section ref={shellRef} className="panel overflow-hidden bg-surface">
          <div className="flex items-center justify-between border-b border-hairline px-4 h-11">
            <div className="flex items-center gap-2">
              <span
                className={`h-2 w-2 rounded-full ${alive && !paused ? "dot-live" : ""}`}
                style={{ background: alive ? "var(--ok)" : "var(--ink-subtle)" }}
              />
              <span className="mono text-[12px] text-ink">{cameraId}</span>
              <span className="label-micro">
                {paused ? "paused" : alive ? "receiving" : "no signal"}
              </span>
            </div>
            <span className="label-micro">
              green box = plate located · label = current consensus
            </span>
          </div>

          <div className="flex items-center justify-center bg-[#0b0b0d]"
               style={{ minHeight: fullscreen ? "calc(100vh - 44px)" : 460 }}>
            {src ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={src} alt={`Live annotated view from ${cameraId}`}
                   className="max-h-full max-w-full object-contain" />
            ) : (
              <div className="px-6 py-20 text-center">
                <div className="label-micro" style={{ color: "var(--ink-subtle)" }}>
                  No frame from this camera
                </div>
                <p className="mt-2 max-w-md text-[12px] text-ink-subtle">
                  Start its worker:
                  <code className="mono block mt-2 text-[11px]">
                    python scripts/run_cameras.py --only {cameraId}
                  </code>
                </p>
              </div>
            )}
          </div>
        </section>

        {/* ---- what this camera has read ---- */}
        <div className="space-y-6">
          <section className="panel overflow-hidden">
            <PanelHeader title="Plates read here">
              <span className="label-micro">{sightings.length} recent</span>
            </PanelHeader>
            <div className="max-h-[420px] overflow-y-auto">
              {sightings.length === 0 ? (
                <EmptyState
                  title="Nothing read yet"
                  hint="Plates appear here the moment voting settles and the record is written to the database."
                />
              ) : (
                <table className="w-full border-collapse">
                  <thead className="table-head">
                    <tr><th className="num">Time</th><th>Plate</th>
                        <th className="num">Conf.</th><th className="num">Frames</th></tr>
                  </thead>
                  <tbody>
                    {sightings.map((s) => (
                      <tr key={`${s.id}-${s.seen_at}`}
                          className={`table-row ${freshId === s.id ? "row-new" : ""}`}>
                        <td className="num text-[12px] text-ink-muted whitespace-nowrap"
                            title={dateTime(s.seen_at)}>
                          {timeOnly(s.seen_at)}
                        </td>
                        <td><Plate value={s.plate} /></td>
                        <td><Confidence value={s.confidence} /></td>
                        <td className="num text-[12px] text-ink-subtle">{s.frame_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>

          <section className="panel overflow-hidden">
            <PanelHeader title="Alerts from this camera" />
            <div className="divide-y divide-hairline max-h-[260px] overflow-y-auto">
              {alerts.length === 0 ? (
                <EmptyState title="No alerts" />
              ) : (
                alerts.map((a) => (
                  <Link key={a.id} href={`/alerts/${a.id}`}
                        className="block px-4 py-2.5 hover:bg-surface-subtle">
                    <div className="flex items-center gap-2">
                      <SeverityPill severity={a.severity} />
                      <span className="label-micro">{ALERT_LABEL[a.alert_type]}</span>
                    </div>
                    <div className="mt-1 text-[13px] text-ink truncate">{a.title}</div>
                    <div className="mt-0.5 text-[11px] text-ink-subtle">
                      {ago(a.occurred_at)}
                    </div>
                  </Link>
                ))
              )}
            </div>
          </section>
        </div>
      </div>
    </>
  );
}
