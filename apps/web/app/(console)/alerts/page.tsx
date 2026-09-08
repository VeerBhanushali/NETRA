"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { PageHeader } from "@/components/Shell";
import { EmptyState, ErrorState, Plate, SeverityPill, SkeletonTable, StatusPill }
  from "@/components/ui";
import { api, type Alert } from "@/lib/api";
import { ALERT_LABEL, ago, dateTime } from "@/lib/format";
import { useLive } from "@/lib/useLive";

const TYPES = ["", "cloned_plate", "speeding", "loitering", "watchlist_hit", "anomaly"];
const STATUSES = ["", "new", "acknowledged", "investigating", "resolved", "false_positive"];

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setLoading(true);
    api.alerts({ alert_type: type, status, limit: 200 })
      .then((r) => { setAlerts(r.results); setError(null); })
      .catch(setError)
      .finally(() => setLoading(false));
  }, [type, status]);

  useEffect(load, [load]);

  useLive((e) => {
    if (e.event === "alert.created") setAlerts((prev) => [e.data as Alert, ...prev]);
    if (e.event === "alert.updated") {
      setAlerts((prev) => prev.map((a) => (a.id === e.data.id ? { ...a, ...e.data } : a)));
    }
  });

  const acknowledge = async (id: number) => {
    await api.updateAlert(id, "acknowledged");
    setAlerts((prev) =>
      prev.map((a) => (a.id === id ? { ...a, status: "acknowledged" } : a)));
  };

  return (
    <>
      <PageHeader
        title="Alerts"
        description="Every alert carries the measurements that produced it."
      >
        <select className="input w-auto h-8 text-[13px]" value={type}
                onChange={(e) => setType(e.target.value)}>
          {TYPES.map((t) => (
            <option key={t} value={t}>{t ? ALERT_LABEL[t] : "All types"}</option>
          ))}
        </select>
        <select className="input w-auto h-8 text-[13px]" value={status}
                onChange={(e) => setStatus(e.target.value)}>
          {STATUSES.map((s) => (
            <option key={s} value={s}>{s ? s.replace("_", " ") : "All statuses"}</option>
          ))}
        </select>
        <button className="btn" onClick={load}>Refresh</button>
      </PageHeader>

      <div className="p-6">
        <div className="panel overflow-hidden">
          {loading ? (
            <SkeletonTable rows={9} cols={5} />
          ) : error ? (
            <ErrorState error={error} onRetry={load} />
          ) : alerts.length === 0 ? (
            <EmptyState
              title="No alerts match this filter"
              hint="Zero false positives is the goal — an empty list is a good sign."
            />
          ) : (
            <table className="w-full border-collapse">
              <thead className="table-head">
                <tr>
                  <th>Severity</th><th>Type</th><th>Plate</th><th>Summary</th>
                  <th>Location</th><th className="num">When</th>
                  <th>Status</th><th></th>
                </tr>
              </thead>
              <tbody>
                {alerts.map((a) => (
                  <tr key={a.id} className="table-row"
                      data-urgent={a.status === "new" && a.severity === "critical"}>
                    <td><SeverityPill severity={a.severity} /></td>
                    <td className="text-[12px] text-ink-muted whitespace-nowrap">
                      {ALERT_LABEL[a.alert_type] ?? a.alert_type}
                    </td>
                    <td>{a.plate ? <Plate value={a.plate} /> :
                         <span className="text-ink-subtle text-[12px]">—</span>}</td>
                    <td className="max-w-[420px]">
                      <Link href={`/alerts/${a.id}`}
                            className="text-[13px] text-ink hover:underline underline-offset-2">
                        {a.title}
                      </Link>
                    </td>
                    <td className="text-[12px] text-ink-muted whitespace-nowrap">
                      {a.camera_name ?? "—"}
                    </td>
                    <td className="num text-[12px] text-ink-muted whitespace-nowrap"
                        title={dateTime(a.occurred_at)}>
                      {ago(a.occurred_at)}
                    </td>
                    <td><StatusPill status={a.status} /></td>
                    <td className="text-right">
                      {a.status === "new" && (
                        <button className="btn btn-ghost h-7 text-[12px]"
                                onClick={() => acknowledge(a.id)}>
                          Acknowledge
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  );
}
