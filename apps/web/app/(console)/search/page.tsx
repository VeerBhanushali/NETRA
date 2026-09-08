"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { PageHeader } from "@/components/Shell";
import { EmptyState, ErrorState, Plate } from "@/components/ui";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";

export default function SearchPage() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [reason, setReason] = useState("");
  const [results, setResults] = useState<any[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const run = async (e: React.FormEvent) => {
    e.preventDefault();
    if (q.trim().length < 2) return;
    setLoading(true);
    setError(null);
    try {
      const r = await api.search(q.trim(), reason.trim() || undefined);
      setResults(r.results);
      // One exact hit is almost always what the operator wanted.
      if (r.results.length === 1) router.push(`/vehicle/${r.results[0].plate}`);
    } catch (e) {
      setError(e);
      setResults(null);
    } finally { setLoading(false); }
  };

  return (
    <>
      <PageHeader
        title="Vehicle search"
        description="Every search is written to the audit log with the stated reason."
      />

      <div className="p-6 space-y-6">
        <form onSubmit={run} className="panel p-5">
          <div className="grid gap-4 md:grid-cols-[minmax(0,320px)_1fr_auto] md:items-end">
            <div>
              <label className="label-micro" htmlFor="q">Plate (full or partial)</label>
              <input
                id="q" autoFocus className="input mono mt-1.5 uppercase"
                placeholder="PB65AR4412" value={q}
                onChange={(e) => setQ(e.target.value.toUpperCase())}
              />
            </div>
            <div>
              <label className="label-micro" htmlFor="reason">
                Reason for search (recorded)
              </label>
              <input
                id="reason" className="input mt-1.5"
                placeholder="FIR 118/2026 — stolen vehicle enquiry"
                value={reason} onChange={(e) => setReason(e.target.value)}
              />
            </div>
            <button className="btn btn-primary h-[34px]" disabled={loading || q.trim().length < 2}>
              {loading ? "Searching…" : "Search"}
            </button>
          </div>
          <p className="mt-3 text-[11px] text-ink-subtle">
            Searching a plate is a consequential act. The operator, the query and the
            reason are recorded before results are returned.
          </p>
        </form>

        {error ? (
          <div className="panel"><ErrorState error={error} /></div>
        ) : null}

        {results !== null && (
          <div className="panel overflow-hidden">
            {results.length === 0 ? (
              <EmptyState title="No vehicle matches that plate"
                          hint="Try a shorter fragment — partial matches are supported." />
            ) : (
              <table className="w-full border-collapse">
                <thead className="table-head">
                  <tr>
                    <th>Plate</th><th className="num">Sightings</th>
                    <th className="num">First seen</th><th className="num">Last seen</th>
                    <th>Colour</th><th>Type</th><th>Flags</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((v) => (
                    <tr key={v.plate} className="table-row">
                      <td><Plate value={v.plate} /></td>
                      <td className="num text-[12px]">{v.sighting_count}</td>
                      <td className="num text-[12px] text-ink-muted">{ago(v.first_seen_at)}</td>
                      <td className="num text-[12px] text-ink-muted">{ago(v.last_seen_at)}</td>
                      <td className="text-[12px] text-ink-muted">{v.color ?? "—"}</td>
                      <td className="text-[12px] text-ink-muted">{v.vehicle_type ?? "—"}</td>
                      <td>
                        <div className="flex gap-1.5">
                          {v.on_watchlist ? (
                            <span className="pill" style={{ color: "var(--critical)",
                                                            background: "var(--critical-wash)" }}>
                              watchlist
                            </span>
                          ) : null}
                          {v.alert_count > 0 ? (
                            <span className="pill" style={{ color: "var(--high)",
                                                            background: "var(--high-wash)" }}>
                              {v.alert_count} alert{v.alert_count > 1 ? "s" : ""}
                            </span>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </>
  );
}
