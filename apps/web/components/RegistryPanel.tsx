"use client";

import { useEffect, useState } from "react";
import { PanelHeader } from "@/components/ui";

/** Owner, RC, insurance, PUC and fitness for one plate.
 *
 *  Collapsed by default and gated behind a typed reason. A plate is a
 *  public identifier; the person attached to it is not, and an owner
 *  panel that loads automatically on page view turns every casual click
 *  into a lookup of somebody's personal data.
 *
 *  The reason is sent to the API, which writes it to audit_log before it
 *  queries anything.
 */
type Status = { provider: string; configured: boolean; is_demo_data: boolean; note: string };

const badge = (s?: string) =>
  s === "valid" ? "var(--ok)" : s === "expired" ? "var(--danger)" : "var(--ink-subtle)";

export function RegistryPanel({ plate }: { plate: string }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [reason, setReason] = useState("");
  const [record, setRecord] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/v1/registry/status")
      .then((r) => r.json()).then(setStatus).catch(() => {});
  }, []);

  const lookup = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(
        `/api/v1/registry/${encodeURIComponent(plate)}?reason=${encodeURIComponent(reason)}`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail ?? `HTTP ${r.status}`);
      setRecord(d);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setLoading(false);
    }
  };

  const doc = (label: string, d: any) => (
    <div className="flex items-baseline justify-between py-1">
      <span className="text-[12px] text-ink">{label}</span>
      <span className="text-[11px]">
        <span className="mono" style={{ color: badge(d?.status) }}>
          {d?.status ?? "unknown"}
        </span>
        {d?.valid_until && (
          <span className="mono ml-2 text-ink-subtle">to {d.valid_until}</span>
        )}
      </span>
    </div>
  );

  return (
    <section className="panel overflow-hidden">
      <PanelHeader title="Owner & documents">
        {status && (
          <span className="label-micro"
                style={{ color: status.is_demo_data ? "var(--warn)" : "var(--ok)" }}>
            {status.is_demo_data ? "demo data" : status.provider}
          </span>
        )}
      </PanelHeader>

      {!record ? (
        <div className="p-4">
          {status?.is_demo_data && (
            <p className="mb-3 p-2 text-[11px]"
               style={{ background: "var(--surface-sunken)",
                        borderLeft: "3px solid var(--warn)", color: "var(--ink)" }}>
              This deployment returns <strong>synthetic</strong> records, not real
              registration data. Set <span className="mono">NETRA_RC_PROVIDER=http</span>{" "}
              with a licensed RC API key for live VAHAN-derived data.
            </p>
          )}
          <label className="label-micro" htmlFor="rc-reason">
            Reason for lookup (recorded in the audit log)
          </label>
          <input id="rc-reason" className="input mt-1 w-full"
                 placeholder="e.g. FIR 214/2026 — hit and run, Sector 17"
                 value={reason} onChange={(e) => setReason(e.target.value)} />
          <button className="btn btn-primary mt-3 w-full"
                  disabled={reason.trim().length < 8 || loading}
                  onClick={lookup}>
            {loading ? "Looking up…" : "Look up owner"}
          </button>
          {reason.length > 0 && reason.trim().length < 8 && (
            <p className="mt-2 text-[11px] text-ink-subtle">
              Give a real reason — a case number or an incident.
            </p>
          )}
          {error && (
            <p className="mt-2 text-[11px]" style={{ color: "var(--danger)" }}>{error}</p>
          )}
        </div>
      ) : (
        <div className="p-4">
          {record.is_demo_data && (
            <p className="mb-3 p-2 text-[11px]"
               style={{ background: "var(--surface-sunken)",
                        borderLeft: "3px solid var(--warn)" }}>
              DEMO DATA — generated from the plate, not a real registration.
            </p>
          )}

          <div className="text-[15px] font-semibold text-ink">{record.owner_masked}</div>
          <div className="text-[11px] text-ink-subtle">
            {record.address_masked ?? "address withheld"}
            {record.owner_serial ? ` · owner #${record.owner_serial}` : ""}
          </div>

          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[12px]">
            {[["Vehicle", [record.maker, record.model].filter(Boolean).join(" ")],
              ["Class", record.vehicle_class], ["Fuel", record.fuel],
              ["Colour", record.colour], ["RTO", record.rto],
              ["Registered", record.registered_at],
              ["Chassis", record.chassis_masked], ["Engine", record.engine_masked],
              ["RC status", record.rc_status], ["Blacklist", record.blacklist],
            ].filter(([, v]) => v).map(([k, v]) => (
              <div key={k as string}>
                <dt className="label-micro">{k}</dt>
                <dd className="text-ink truncate">{v as string}</dd>
              </div>
            ))}
          </dl>

          <div className="mt-3 border-t border-hairline pt-2">
            {doc("PUC", record.puc)}
            {doc("Insurance", record.insurance)}
            {doc("Fitness", record.fitness)}
            {record.insurance?.insurer && (
              <div className="text-[11px] text-ink-subtle">{record.insurance.insurer}</div>
            )}
          </div>

          {/* The part a purchased API cannot tell you. */}
          <div className="mt-3 border-t border-hairline pt-2 text-[11px] text-ink-subtle">
            Seen by NETRA <span className="mono text-ink">{record.netra.sightings}</span>{" "}
            time{record.netra.sightings === 1 ? "" : "s"}
            {record.netra.last_seen && ` · last ${record.netra.last_seen.slice(0, 16).replace("T", " ")}`}
            {record.netra.watchlisted && (
              <span style={{ color: "var(--danger)" }}> · on watchlist</span>
            )}
          </div>

          <p className="mt-2 text-[11px] text-ink-subtle">{record.retention}</p>
          <button className="btn mt-3 w-full" onClick={() => { setRecord(null); setReason(""); }}>
            Close
          </button>
        </div>
      )}
    </section>
  );
}
