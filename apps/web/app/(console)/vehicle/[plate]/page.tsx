"use client";

import { use, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { PageHeader } from "@/components/Shell";
import { RegistryPanel } from "@/components/RegistryPanel";
import { Confidence, EmptyState, ErrorState, PanelHeader, SeverityPill }
  from "@/components/ui";
import { api, type Alert, type Trajectory } from "@/lib/api";
import { ALERT_LABEL, dateTime, duration, metres, timeOnly } from "@/lib/format";

const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

export default function VehiclePage({ params }: { params: Promise<{ plate: string }> }) {
  const { plate: raw } = use(params);
  const plate = decodeURIComponent(raw).toUpperCase();

  const [traj, setTraj] = useState<Trajectory | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [step, setStep] = useState<number | null>(null);   // playback position

  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    // The trajectory is the point of this screen; if it fails the screen
    // has nothing to say, so that failure is surfaced. The detail panel
    // is supplementary and degrades quietly.
    api.trajectory(plate)
      .then((t) => { setTraj(t); setError(null); })
      .catch((e) => { setTraj(null); setError(e); });
    api.vehicle(plate).then(setDetail).catch(() => setDetail(null));
  }, [plate]);

  const points = traj?.points ?? [];
  // The scrubber reveals the path chronologically — this is the moment
  // that makes the whole system legible to someone watching a demo.
  const shown = step === null ? points : points.slice(0, step + 1);
  const alerts: Alert[] = detail?.alerts ?? [];
  const watch = detail?.watchlist;

  return (
    <>
      <PageHeader title={plate} description="Movement history across the camera network">
        <Link href="/search" className="btn">New search</Link>
      </PageHeader>

      <div className="p-6 space-y-6">
        {error ? (
          <div className="panel"><ErrorState error={error} /></div>
        ) : null}

        {watch && (
          <div className="panel px-5 py-3" style={{ borderColor: "var(--critical)",
                                                    background: "var(--critical-wash)" }}>
            <div className="flex items-center gap-2">
              <SeverityPill severity={watch.severity} />
              <span className="text-[13px] font-semibold" style={{ color: "var(--critical)" }}>
                On watchlist
              </span>
            </div>
            <p className="mt-1 text-[13px] text-ink-body">
              {watch.reason}
              {watch.case_ref ? <span className="mono"> · {watch.case_ref}</span> : null}
            </p>
          </div>
        )}

        <div className="grid gap-6 xl:grid-cols-[1.3fr_1fr]">
          <section className="panel overflow-hidden">
            <PanelHeader title="Trajectory">
              <span className="label-micro">{points.length} stops</span>
            </PanelHeader>
            <div style={{ height: 460 }}>
              <MapView points={shown} height="460px" />
            </div>
            {points.length > 1 && (
              <div className="border-t border-hairline px-4 py-3">
                <div className="flex items-center gap-3">
                  <span className="label-micro shrink-0">Playback</span>
                  <input
                    type="range" min={0} max={points.length - 1}
                    value={step ?? points.length - 1}
                    onChange={(e) => setStep(Number(e.target.value))}
                    className="w-full accent-[color:var(--accent)]"
                    aria-label="Trajectory playback position"
                  />
                  <button className="btn btn-ghost h-7 text-[12px] shrink-0"
                          onClick={() => setStep(null)}>
                    Show all
                  </button>
                </div>
                {step !== null && points[step] && (
                  <div className="mt-2 text-[12px] text-ink-muted">
                    Stop {step + 1}: <span className="text-ink">{points[step].camera_name}</span>
                    {" · "}
                    <span className="mono">{dateTime(points[step].seen_at)}</span>
                  </div>
                )}
              </div>
            )}
          </section>

          <div className="space-y-6">
            <RegistryPanel plate={plate} />

            <section className="panel overflow-hidden">
              <PanelHeader title="Alerts for this vehicle" />
              <div className="divide-y divide-hairline max-h-[220px] overflow-y-auto">
                {alerts.length === 0 ? (
                  <EmptyState title="No alerts" />
                ) : (
                  alerts.map((a) => (
                    <Link key={a.id} href={`/alerts/${a.id}`}
                          className="block px-4 py-2.5 hover:bg-surface-subtle">
                      <div className="flex items-center gap-2">
                        <SeverityPill severity={a.severity} />
                        <span className="text-[13px] text-ink">
                          {ALERT_LABEL[a.alert_type]}
                        </span>
                      </div>
                      <div className="mt-0.5 text-[11px] text-ink-subtle">
                        {dateTime(a.occurred_at)}
                      </div>
                    </Link>
                  ))
                )}
              </div>
            </section>

            <section className="panel overflow-hidden">
              <PanelHeader title="Sighting log" />
              <div className="max-h-[420px] overflow-y-auto">
                {points.length === 0 ? (
                  <EmptyState title="No sightings recorded" />
                ) : (
                  <table className="w-full border-collapse">
                    <thead className="table-head">
                      <tr><th className="num">#</th><th className="num">Time</th>
                          <th>Camera</th><th className="num">Conf.</th></tr>
                    </thead>
                    <tbody>
                      {points.map((p, i) => (
                        <tr key={p.id} className="table-row cursor-pointer"
                            onClick={() => setStep(i)}>
                          <td className="num text-[12px] text-ink-subtle">{i + 1}</td>
                          <td className="num text-[12px] whitespace-nowrap">
                            {timeOnly(p.seen_at)}
                          </td>
                          <td className="text-[12px] text-ink-muted">{p.camera_name}</td>
                          <td><Confidence value={p.confidence} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </section>
          </div>
        </div>

        {traj && traj.legs.length > 0 && (
          <section className="panel overflow-hidden">
            <PanelHeader title="Segments">
              <span className="label-micro">
                implied speed is straight-line — indicative only
              </span>
            </PanelHeader>
            <table className="w-full border-collapse">
              <thead className="table-head">
                <tr><th>From</th><th>To</th><th className="num">Distance</th>
                    <th className="num">Elapsed</th>
                    <th className="num">Implied speed</th></tr>
              </thead>
              <tbody>
                {traj.legs.map((l, i) => {
                  const fast = (l.implied_kmh ?? 0) > 90;
                  return (
                    <tr key={i} className="table-row">
                      <td className="mono text-[12px]">{l.from_camera}</td>
                      <td className="mono text-[12px]">{l.to_camera}</td>
                      <td className="num text-[12px]">{metres(l.distance_m)}</td>
                      <td className="num text-[12px]">{duration(l.elapsed_s)}</td>
                      <td className="num text-[12px]"
                          style={{ color: fast ? "var(--high)" : "var(--ink-muted)" }}>
                        {l.implied_kmh !== null ? `${l.implied_kmh} km/h` : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
        )}
      </div>
    </>
  );
}
