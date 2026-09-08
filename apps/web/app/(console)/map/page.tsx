"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { PageHeader } from "@/components/Shell";
import { ErrorState, SeverityPill } from "@/components/ui";
import { api, type Alert, type Camera } from "@/lib/api";
import { ALERT_LABEL, ago } from "@/lib/format";
import { useLive } from "@/lib/useLive";

const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

export default function MapPage() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);

  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.cameras()
      .then((r) => { setCameras(r.results); setError(null); })
      .catch(setError);
    api.alerts({ status: "new", limit: 100 })
      .then((r) => setAlerts(r.results))
      .catch(() => {});
  }, []);

  useLive((e) => {
    if (e.event === "alert.created") setAlerts((p) => [e.data as Alert, ...p]);
  });

  const incidents = alerts
    .filter((a) => a.lat != null && a.lon != null)
    .map((a) => ({ lat: a.lat!, lon: a.lon!, severity: a.severity, title: a.title }));

  return (
    <>
      <PageHeader title="Live map"
                  description={`${cameras.length} cameras · ${incidents.length} open incidents`} />
      <div className="flex h-[calc(100vh-var(--header-h))]">
        <div className="flex-1">
          <MapView cameras={cameras} incidents={incidents} height="100%" />
        </div>
        <aside className="w-[320px] shrink-0 overflow-y-auto border-l border-hairline">
          <div className="sticky top-0 border-b border-hairline bg-surface px-4 h-11 flex items-center">
            <h2 className="text-[13px] font-semibold text-ink">Open incidents</h2>
          </div>
          {error ? <ErrorState error={error} compact /> : null}
          <div className="divide-y divide-hairline">
            {alerts.length === 0 ? (
              <p className="p-4 text-[12px] text-ink-subtle">Nothing open.</p>
            ) : (
              alerts.map((a) => (
                <Link key={a.id} href={`/alerts/${a.id}`}
                      className="block px-4 py-3 hover:bg-surface-subtle">
                  <div className="flex items-center gap-2">
                    <SeverityPill severity={a.severity} />
                    <span className="label-micro">{ALERT_LABEL[a.alert_type]}</span>
                  </div>
                  <div className="mt-1 text-[13px] text-ink">{a.title}</div>
                  <div className="mt-0.5 text-[11px] text-ink-subtle">
                    {a.camera_name} · {ago(a.occurred_at)}
                  </div>
                </Link>
              ))
            )}
          </div>
        </aside>
      </div>
    </>
  );
}
