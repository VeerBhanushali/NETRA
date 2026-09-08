"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { PageHeader } from "@/components/Shell";
import { Confidence, EmptyState, ErrorState, PanelHeader, Plate, SeverityPill }
  from "@/components/ui";
import { OperatorCamera } from "@/components/OperatorCamera";
import { api, type Alert, type Camera, type Sighting } from "@/lib/api";
import { ALERT_LABEL, ago, timeOnly } from "@/lib/format";
import { useLive } from "@/lib/useLive";

/** The live wall.
 *
 *  Each tile polls the latest annotated JPEG the edge worker writes for
 *  that camera. Polling a still frame rather than streaming MJPEG/HLS is
 *  a deliberate trade: it is a few lines of code, survives a worker
 *  restart without a stalled stream, and costs a fraction of the
 *  bandwidth — and at 2 frames a second it reads as live.
 */
function CameraTile({ camera, fps, big }: { camera: Camera; fps: number; big?: boolean }) {
  const [src, setSrc] = useState<string | null>(null);
  const [alive, setAlive] = useState(false);

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    let misses = 0;

    const tick = () => {
      if (stop) return;
      const url = `/evidence/live/${camera.id}.jpg?t=${Date.now()}`;
      const img = new Image();
      img.onload = () => {
        if (stop) return;
        misses = 0;
        setSrc(url);
        setAlive(true);
        schedule();
      };
      img.onerror = () => {
        if (stop) return;
        // Back off on a camera with no worker. Polling a 404 at full rate
        // forever spams thousands of failed requests and eventually wedges
        // the renderer — which looked like the whole app glitching.
        misses += 1;
        setAlive(false);
        schedule();
      };
      img.src = url;
    };

    const schedule = () => {
      if (stop) return;
      const base = Math.max(200, 1000 / fps);
      const delay = misses === 0 ? base : Math.min(15000, base * 2 ** misses);
      timer = setTimeout(tick, delay);
    };

    tick();
    return () => { stop = true; clearTimeout(timer); };
  }, [camera.id, fps]);

  return (
    <Link href={`/live/${camera.id}`} className="block">
    <figure className="panel tile overflow-hidden cursor-pointer"
            style={{ ["--tile-hue" as any]: alive ? "var(--ok)" : "var(--border-strong)" }}>
      <div
        className="relative flex items-center justify-center bg-surface-sunken"
        style={{ aspectRatio: "3 / 2" }}
      >
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={src} alt={`Live view from ${camera.name}`}
               className="h-full w-full object-cover" />
        ) : (
          <div className="text-center px-4">
            <div className="label-micro">No live frame</div>
            <p className="mt-1 text-[11px] text-ink-subtle">
              Start a worker to populate this tile
            </p>
          </div>
        )}
        <span
          className="absolute left-2 top-2 flex items-center gap-1.5 px-1.5 py-0.5"
          style={{ background: "var(--scrim)", border: "1px solid var(--border)" }}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${alive ? "dot-live" : ""}`}
                style={{ background: alive ? "var(--ok)" : "var(--ink-subtle)" }} />
          <span className="mono text-[10px] text-ink">{camera.id}</span>
        </span>
      </div>
      <figcaption className="flex items-center justify-between border-t border-hairline px-3 py-2">
        <span className="text-[12px] text-ink truncate">{camera.name}</span>
        <span className="mono text-[10px] text-ink-subtle shrink-0">
          {camera.last_sighting_at ? ago(camera.last_sighting_at) : "no data"}
        </span>
      </figcaption>
    </figure>
    </Link>
  );
}

export default function LivePage() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [offline, setOffline] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const [sightings, setSightings] = useState<Sighting[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [fps, setFps] = useState(2);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    const loadCameras = () => api.cameras().then((r) => {
      // Only cameras actually producing frames belong on the wall. A tile
      // that can never show video is not information, it is clutter that
      // reads as a fault. The count of the rest is surfaced in the header
      // instead, so nothing is silently hidden.
      // cam-live is rendered by <OperatorCamera/> below, which owns the
      // device. Polling its stored JPEG as well would put the same feed
      // on the wall twice, a frame or two apart.
      setCameras(r.results.filter((c) => c.is_streaming && c.id !== "cam-live"));
      setOffline(r.count - r.streaming);
      setError(null);
    }).catch(setError);
    loadCameras();
    // Cameras come and go as workers start and stop.
    const id = setInterval(loadCameras, 10000);
    api.sightings(30).then((r) => setSightings(r.results)).catch(() => {});
    api.alerts({ limit: 6 }).then((r) => setAlerts(r.results)).catch(() => {});
    return () => clearInterval(id);
  }, []);

  const connected = useLive((e) => {
    if (paused) return;
    if (e.event === "sighting.created")
      setSightings((p) => [e.data as Sighting, ...p].slice(0, 30));
    if (e.event === "alert.created")
      setAlerts((p) => [e.data as Alert, ...p].slice(0, 6));
  });

  // Cameras that have actually produced data lead the wall.
  const ordered = useMemo(
    () => [...cameras].sort((a, b) =>
      (b.last_sighting_at ?? "").localeCompare(a.last_sighting_at ?? "")),
    [cameras]
  );

  return (
    <>
      <PageHeader
        title="Live feed"
        description={
          offline > 0
            ? `${cameras.length} live · ${offline} camera${offline > 1 ? "s" : ""} without a worker`
            : "Annotated frames straight from the vision pipeline"
        }
      >
        <span className="flex items-center gap-1.5">
          <span className="h-1.5 w-1.5 rounded-full"
                style={{ background: connected ? "var(--ok)" : "var(--ink-subtle)" }} />
          <span className="text-[11px] text-ink-subtle">
            {connected ? "stream connected" : "disconnected"}
          </span>
        </span>
        <select className="input w-auto h-8 text-[13px]" value={fps}
                onChange={(e) => setFps(Number(e.target.value))}
                aria-label="Refresh rate">
          <option value={1}>1 fps</option>
          <option value={2}>2 fps</option>
          <option value={4}>4 fps</option>
        </select>
        <button className="btn" onClick={() => setPaused((p) => !p)}>
          {paused ? "Resume" : "Pause"}
        </button>
      </PageHeader>

      <div className="grid gap-6 p-6 xl:grid-cols-[1.6fr_1fr]">
        <section>
          {error ? (
            <div className="panel"><ErrorState error={error} /></div>
          ) : ordered.length === 0 ? (
            // The operator camera still belongs here: it needs no worker,
            // so a wall with no recorded feeds is not a dead end.
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="panel">
                <EmptyState
                  title="No recorded cameras are streaming"
                  hint="Start the network with: python scripts/start.py"
                />
              </div>
              <OperatorCamera />
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
              {ordered.map((c) => (
                <CameraTile key={c.id} camera={c} fps={paused ? 0.01 : fps} />
              ))}
              <OperatorCamera />
            </div>
          )}
        </section>

        <div className="space-y-6">
          <section className="panel overflow-hidden">
            <PanelHeader title="Detections">
              <span className="label-micro">live</span>
            </PanelHeader>
            <div className="max-h-[340px] overflow-y-auto">
              {sightings.length === 0 ? (
                <EmptyState title="Nothing detected yet" />
              ) : (
                <table className="w-full border-collapse">
                  <thead className="table-head">
                    <tr><th className="num">Time</th><th>Plate</th>
                        <th>Camera</th><th className="num">Conf.</th></tr>
                  </thead>
                  <tbody>
                    {sightings.map((s) => (
                      <tr key={`${s.id}-${s.seen_at}`} className="table-row">
                        <td className="num text-[12px] text-ink-muted">{timeOnly(s.seen_at)}</td>
                        <td><Plate value={s.plate} /></td>
                        <td className="text-[12px] text-ink-muted">{s.camera_id}</td>
                        <td><Confidence value={s.confidence} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>

          <section className="panel overflow-hidden">
            <PanelHeader title="Alerts">
              <Link href="/alerts" className="text-[12px]" style={{ color: "var(--accent)" }}>
                All
              </Link>
            </PanelHeader>
            <div className="divide-y divide-hairline max-h-[280px] overflow-y-auto">
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
