"use client";

import { use, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { PageHeader } from "@/components/Shell";
import { ErrorState, PanelHeader, Plate, SeverityPill, SkeletonTable, StatusPill }
  from "@/components/ui";
import { api, type Alert } from "@/lib/api";
import { ALERT_LABEL, dateTime, duration, metres } from "@/lib/format";

const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

/** Renders one evidence key/value. The whole point of this screen is that
 *  an operator can interrogate *why* the system made an accusation. */
function EvidenceRow({ k, v }: { k: string; v: any }) {
  const label = k.replace(/_/g, " ");
  let rendered: React.ReactNode;

  if (v === null || v === undefined) rendered = <span className="text-ink-subtle">—</span>;
  else if (Array.isArray(v)) rendered = <span className="mono text-[12px]">{v.join(", ")}</span>;
  else if (typeof v === "object")
    rendered = (
      <span className="mono text-[12px]">
        {Object.entries(v).map(([kk, vv]) => `${kk}: ${vv}`).join("  ·  ")}
      </span>
    );
  else if (k.endsWith("_m")) rendered = <span className="mono">{metres(Number(v))}</span>;
  else if (k.endsWith("_s")) rendered = <span className="mono">{duration(Number(v))}</span>;
  else rendered = <span className="mono">{String(v)}</span>;

  return (
    <div className="flex items-baseline justify-between gap-6 border-b border-hairline py-2 last:border-0">
      <span className="label-micro shrink-0">{label}</span>
      <span className="text-[13px] text-ink text-right">{rendered}</span>
    </div>
  );
}

export default function AlertDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [alert, setAlert] = useState<Alert | null>(null);
  const [busy, setBusy] = useState(false);

  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.alert(Number(id))
      .then((a) => { setAlert(a); setError(null); })
      .catch((e) => { setAlert(null); setError(e); });
  }, [id]);

  const setStatus = async (status: any) => {
    setBusy(true);
    try {
      const updated = await api.updateAlert(Number(id), status);
      setAlert((a) => (a ? { ...a, ...updated } : a));
    } finally { setBusy(false); }
  };

  if (error) {
    return (
      <>
        <PageHeader title="Alert" />
        <div className="p-6">
          <div className="panel"><ErrorState error={error} /></div>
        </div>
      </>
    );
  }
  if (!alert) {
    return (
      <>
        <PageHeader title="Alert" />
        <div className="p-6"><div className="panel"><SkeletonTable rows={6} cols={3} /></div></div>
      </>
    );
  }

  const points = alert.recent_sightings ?? [];
  const incidents = alert.lat && alert.lon
    ? [{ lat: alert.lat, lon: alert.lon, severity: alert.severity, title: alert.title }]
    : [];

  return (
    <>
      <PageHeader title={ALERT_LABEL[alert.alert_type] ?? "Alert"}
                  description={`Alert #${alert.id}`}>
        {alert.status === "new" && (
          <button className="btn" disabled={busy} onClick={() => setStatus("acknowledged")}>
            Acknowledge
          </button>
        )}
        <button className="btn" disabled={busy} onClick={() => setStatus("investigating")}>
          Investigate
        </button>
        <button className="btn" disabled={busy} onClick={() => setStatus("resolved")}>
          Resolve
        </button>
        {/* Marking a false positive is how thresholds get tuned, so it is a
            first-class action, not hidden in a menu. */}
        <button className="btn" disabled={busy} onClick={() => setStatus("false_positive")}>
          False positive
        </button>
      </PageHeader>

      <div className="p-6 grid gap-6 xl:grid-cols-[1fr_1fr]">
        <div className="space-y-6">
          <section className="panel p-5">
            <div className="flex items-center gap-2">
              <SeverityPill severity={alert.severity} />
              <StatusPill status={alert.status} />
              <span className="label-micro">{dateTime(alert.occurred_at)}</span>
            </div>
            <h2 className="mt-3 text-[18px] font-semibold tracking-tight text-ink">
              {alert.title}
            </h2>
            <p className="mt-2 text-[13px] leading-relaxed text-ink-body">{alert.detail}</p>

            <div className="mt-4 flex flex-wrap gap-x-8 gap-y-2 border-t border-hairline pt-4">
              <div>
                <div className="label-micro">Plate</div>
                {alert.plate ? <Plate value={alert.plate} />
                             : <span className="text-ink-subtle text-[13px]">not readable</span>}
              </div>
              <div>
                <div className="label-micro">Camera</div>
                <div className="text-[13px] text-ink">{alert.camera_name ?? "—"}</div>
              </div>
              <div>
                <div className="label-micro">Confidence</div>
                <div className="mono text-[13px] text-ink">
                  {(alert.confidence * 100).toFixed(1)}%
                </div>
              </div>
            </div>
          </section>

          <section className="panel">
            <PanelHeader title="Evidence" />
            <div className="px-5 py-2">
              {Object.entries(alert.evidence ?? {}).map(([k, v]) => (
                <EvidenceRow key={k} k={k} v={v} />
              ))}
            </div>
          </section>
        </div>

        <div className="space-y-6">
          <section className="panel overflow-hidden">
            <PanelHeader title="Location & recent movement">
              {alert.plate && (
                <Link href={`/vehicle/${alert.plate}`} className="text-[12px]"
                      style={{ color: "var(--accent)" }}>
                  Full trajectory
                </Link>
              )}
            </PanelHeader>
            <div style={{ height: 420 }}>
              <MapView points={points} incidents={incidents} height="420px" />
            </div>
          </section>
        </div>
      </div>
    </>
  );
}
