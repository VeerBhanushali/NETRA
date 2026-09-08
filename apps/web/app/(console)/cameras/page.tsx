"use client";

import { useEffect, useState } from "react";
import { PageHeader } from "@/components/Shell";
import { EmptyState, ErrorState, SkeletonTable } from "@/components/ui";
import { api, type Camera } from "@/lib/api";
import { ago } from "@/lib/format";

export default function CamerasPage() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    api.cameras()
      .then((r) => { setCameras(r.results); setError(null); })
      .catch(setError)
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  // A camera that has produced nothing for 15 minutes is treated as dark.
  // Silent camera failure is the most common way a network like this rots.
  const isStale = (c: Camera) =>
    !c.last_sighting_at ||
    Date.now() - new Date(c.last_sighting_at).getTime() > 15 * 60 * 1000;

  return (
    <>
      <PageHeader
        title="Cameras"
        description={`${cameras.filter((c) => !isStale(c)).length} of ${cameras.length} reporting`}
      />
      <div className="p-6">
        <div className="panel overflow-hidden">
          {loading ? (
            <SkeletonTable rows={8} cols={6} />
          ) : error ? (
            <ErrorState error={error} onRetry={load} />
          ) : cameras.length === 0 ? (
            <EmptyState title="No cameras registered"
                        hint="Run scripts/seed_demo.py to create the demo network." />
          ) : (
            <table className="w-full border-collapse">
              <thead className="table-head">
                <tr>
                  <th>Status</th><th>ID</th><th>Name</th><th>Zone</th>
                  <th>Road</th><th>Coordinates</th>
                  <th className="num">Sightings</th><th className="num">Last seen</th>
                </tr>
              </thead>
              <tbody>
                {cameras.map((c) => {
                  const stale = isStale(c);
                  return (
                    <tr key={c.id} className="table-row">
                      <td>
                        <span className="inline-flex items-center gap-1.5">
                          <span className="h-1.5 w-1.5 rounded-full"
                                style={{ background: stale ? "var(--high)" : "var(--ok)" }} />
                          <span className="text-[12px]"
                                style={{ color: stale ? "var(--high)" : "var(--ink-muted)" }}>
                            {stale ? "no data" : "reporting"}
                          </span>
                        </span>
                      </td>
                      <td className="mono text-[12px]">{c.id}</td>
                      <td className="text-[13px] text-ink">{c.name}</td>
                      <td className="text-[12px] text-ink-muted">
                        {c.zone_name ?? "—"}
                        {c.zone_kind && c.zone_kind !== "monitored" ? (
                          <span className="ml-1.5 pill"
                                style={{ color: "var(--medium)", background: "var(--medium-wash)" }}>
                            {c.zone_kind}
                          </span>
                        ) : null}
                      </td>
                      <td className="text-[12px] text-ink-muted">{c.road_name ?? "—"}</td>
                      <td className="mono text-[11px] text-ink-subtle">
                        {c.lat.toFixed(4)}, {c.lon.toFixed(4)}
                      </td>
                      <td className="num text-[12px]">{c.sighting_count}</td>
                      <td className="num text-[12px] text-ink-muted">
                        {c.last_sighting_at ? ago(c.last_sighting_at) : "never"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  );
}
