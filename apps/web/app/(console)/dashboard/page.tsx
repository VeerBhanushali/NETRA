"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { PageHeader } from "@/components/Shell";
import { Confidence, EmptyState, PanelHeader, Plate, SeverityPill,
         SkeletonTable, StatTile } from "@/components/ui";
import { api, type Alert, type Sighting, type Stats } from "@/lib/api";
import { ALERT_LABEL, ago, pct, timeOnly } from "@/lib/format";
import { useLive } from "@/lib/useLive";
import { Sparkline } from "@/components/Sparkline";

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [sightings, setSightings] = useState<Sighting[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [buckets, setBuckets] = useState<{ hour: string; n: number }[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([api.stats(), api.sightings(40), api.alerts({ limit: 8 }),
                 api.timeline(24)])
      .then(([s, sg, al, tl]) => {
        setStats(s);
        setSightings(sg.results);
        setAlerts(al.results);
        setBuckets(tl.buckets);
        setError(null);
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(load, [load]);

  // Live rows are prepended rather than triggering a refetch — a refetch
  // per sighting would hammer the API during a busy demo.
  useLive((e) => {
    if (e.event === "sighting.created") {
      setSightings((prev) => [e.data as Sighting, ...prev].slice(0, 40));
      setStats((s) => (s ? { ...s, sightings_total: s.sightings_total + 1 } : s));
    }
    if (e.event === "alert.created") {
      setAlerts((prev) => [e.data as Alert, ...prev].slice(0, 8));
      setStats((s) => (s ? { ...s, alerts_open: s.alerts_open + 1 } : s));
    }
  });

  if (error) {
    return (
      <>
        <PageHeader title="Overview" />
        <div className="p-6">
          <div className="panel p-4" style={{ borderColor: "var(--critical)" }}>
            <div className="text-[13px] font-semibold" style={{ color: "var(--critical)" }}>
              Cannot reach the API
            </div>
            <p className="mt-1 text-[12px] text-ink-muted">
              Start it with{" "}
              <code className="mono">uvicorn app.main:app --app-dir apps/api --port 8000</code>
            </p>
            <p className="mono mt-2 text-[11px] text-ink-subtle">{error}</p>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Overview"
        description={stats ? `Updated ${timeOnly(stats.generated_at)} UTC` : "Loading…"}
      >
        <button className="btn" onClick={load}>Refresh</button>
      </PageHeader>

      <div className="p-6 space-y-6">
        {/* ---- KPI row ---- */}
        <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <StatTile tone="traffic" label="Sightings 24h"
                    value={stats?.sightings_24h ?? "—"}
                    sub={`${stats?.sightings_total ?? 0} total`} />
          <StatTile tone="alerts" label="Open alerts" muteWhenZero
                    value={stats?.alerts_open ?? "—"}
                    sub={`${stats?.alerts_total ?? 0} all time`} />
          <StatTile tone="vehicles" label="Vehicles seen"
                    value={stats?.vehicles_total ?? "—"} sub="distinct plates" />
          <StatTile tone="cameras" label="Cameras online"
                    value={stats ? `${stats.cameras_active}/${stats.cameras_total}` : "—"}
                    sub="reporting" />
          <StatTile tone="review" label="Awaiting review" muteWhenZero
                    value={stats?.review_pending ?? "—"} sub="below auto-accept" />
          <StatTile tone="accuracy" label="Auto-accept rate"
                    value={stats ? pct(stats.accuracy.auto_accept_rate, 1) : "—"}
                    sub={`threshold ${stats?.accuracy.auto_accept_threshold ?? "—"}`} />
        </section>

        {/* ---- activity over the last day ---- */}
        <section className="panel">
          <PanelHeader title="Traffic volume">
            <span className="label-micro">last 24 hours</span>
          </PanelHeader>
          <div className="px-4 py-3">
            <Sparkline buckets={buckets} />
          </div>
        </section>

        <div className="grid gap-6 xl:grid-cols-[1.4fr_1fr]">
          {/* ---- live sightings ---- */}
          <section className="panel overflow-hidden">
            <PanelHeader title="Live sightings">
              <span className="label-micro">last 40</span>
            </PanelHeader>
            <div className="max-h-[520px] overflow-y-auto">
              {sightings.length === 0 ? (
                stats === null ? (
                  <SkeletonTable rows={8} cols={5} />
                ) : (
                <EmptyState
                  title="No sightings yet"
                  hint="Start the network with: python scripts/start.py"
                />
                )
              ) : (
                <table className="w-full border-collapse">
                  <thead className="table-head">
                    <tr>
                      <th className="num">Time</th><th>Plate</th><th>Camera</th>
                      <th className="num">Confidence</th>
                      <th className="num">Frames</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sightings.map((s) => (
                      <tr key={`${s.id}-${s.seen_at}`} className="table-row">
                        <td className="num text-[12px] text-ink-muted whitespace-nowrap">
                          {timeOnly(s.seen_at)}
                        </td>
                        <td>
                          <div className="flex items-center gap-2">
                            <Plate value={s.plate} />
                            {s.on_watchlist ? (
                              <span className="pill"
                                    style={{ color: "var(--critical)",
                                             background: "var(--critical-wash)" }}>
                                watchlist
                              </span>
                            ) : null}
                          </div>
                        </td>
                        <td className="text-[12px] text-ink-muted">{s.camera_name}</td>
                        <td><Confidence value={s.confidence} /></td>
                        <td className="num text-[12px] text-ink-subtle">{s.frame_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>

          {/* ---- recent alerts ---- */}
          <section className="panel overflow-hidden">
            <PanelHeader title="Recent alerts">
              <Link href="/alerts" className="text-[12px]" style={{ color: "var(--accent)" }}>
                View all
              </Link>
            </PanelHeader>
            <div className="max-h-[520px] overflow-y-auto divide-y divide-hairline">
              {alerts.length === 0 ? (
                <EmptyState title="No alerts" hint="The rules engine has flagged nothing." />
              ) : (
                alerts.map((a) => (
                  <Link key={a.id} href={`/alerts/${a.id}`}
                        className="block px-4 py-3 hover:bg-surface-subtle">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <SeverityPill severity={a.severity} />
                          <span className="label-micro">{ALERT_LABEL[a.alert_type]}</span>
                        </div>
                        <div className="mt-1.5 text-[13px] font-medium text-ink truncate">
                          {a.title}
                        </div>
                        <div className="mt-0.5 text-[11px] text-ink-subtle">
                          {a.camera_name ?? "—"} · {ago(a.occurred_at)}
                        </div>
                      </div>
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
