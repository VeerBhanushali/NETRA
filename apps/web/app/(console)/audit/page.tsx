"use client";

import { useEffect, useState } from "react";
import { PageHeader } from "@/components/Shell";
import { EmptyState, ErrorState, SkeletonTable } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";

/** The accountability screen.
 *
 *  "Who searched this plate, and why" is the single most important record
 *  a surveillance system keeps. Making it a visible, first-class screen —
 *  rather than a table nobody looks at — is the point. */
export default function AuditPage() {
  const [rows, setRows] = useState<any[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    api.audit(200)
      .then((r) => { setRows(r.results); setError(null); })
      .catch(setError)
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Append-only. Every search, view and watchlist change is recorded."
      />
      <div className="p-6">
        <div className="panel overflow-hidden">
          {loading ? (
            <SkeletonTable rows={9} cols={5} />
          ) : error ? (
            <ErrorState error={error} onRetry={load} />
          ) : rows.length === 0 ? (
            <EmptyState title="No activity recorded yet" />
          ) : (
            <table className="w-full border-collapse">
              <thead className="table-head">
                <tr><th className="num">When</th><th>Actor</th><th>Action</th>
                    <th>Target</th><th>Stated reason</th></tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="table-row">
                    <td className="num text-[12px] text-ink-muted whitespace-nowrap">
                      {dateTime(r.ts)}
                    </td>
                    <td className="text-[12px] text-ink">{r.actor}</td>
                    <td>
                      <span className="pill" style={{ color: "var(--ink-muted)",
                                                      background: "var(--surface-sunken)" }}>
                        {r.action.replace(/_/g, " ")}
                      </span>
                    </td>
                    <td className="mono text-[12px]">{r.target ?? "—"}</td>
                    <td className="text-[12px] text-ink-muted">
                      {r.reason ?? <span className="text-ink-subtle">none given</span>}
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
