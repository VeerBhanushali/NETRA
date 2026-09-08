import Link from "next/link";
import { SEVERITY_STYLE } from "@/lib/format";

/** Severity pill. Colour AND text, always — an operator with colour
 *  vision deficiency must be able to rank two alerts. */
export function SeverityPill({ severity }: { severity: string }) {
  const s = SEVERITY_STYLE[severity] ?? SEVERITY_STYLE.info;
  return (
    <span className="pill" style={{ color: s.color, background: s.background }}>
      {severity}
    </span>
  );
}

export function StatusPill({ status }: { status: string }) {
  const muted = status !== "new";
  return (
    <span
      className="pill"
      style={{
        color: muted ? "var(--ink-subtle)" : "var(--ink-body)",
        background: muted ? "transparent" : "var(--surface-sunken)",
      }}
    >
      {status.replace("_", " ")}
    </span>
  );
}

/** A plate is the primary key of this entire system, so it always looks
 *  the same: monospace, tabular, slashed zero, and a wash chip on hover
 *  rather than an underline — it is an entity, not a hyperlink. */
export function Plate({ value, className = "" }: { value: string; className?: string }) {
  return (
    <Link href={`/vehicle/${encodeURIComponent(value)}`} className={`plate ${className}`}>
      {value}
    </Link>
  );
}

/** Each metric owns a hue, so the eye learns the dashboard's geography:
 *  alerts are always red, review always amber, accuracy always green. */
export type TileTone =
  | "traffic" | "vehicles" | "cameras" | "review" | "accuracy" | "alerts";

export function StatTile({
  label, value, sub, tone = "traffic", muteWhenZero, loading,
}: {
  label: string; value: string | number; sub?: string;
  tone?: TileTone;
  /** Alert-style tiles go grey at zero — nothing to see is good news. */
  muteWhenZero?: boolean;
  loading?: boolean;
}) {
  const quiet = muteWhenZero && Number(value) === 0;
  const hue = quiet ? "var(--ink-subtle)" : `var(--c-${tone})`;
  return (
    <div className="panel tile px-4 py-3" style={{ ["--tile-hue" as any]: hue }}>
      <div className="label-micro">{label}</div>
      {loading ? (
        <div className="skeleton mt-2" style={{ height: 26, width: 68 }} />
      ) : (
        <div className="mono mt-1.5 text-[26px] leading-none font-semibold"
             style={{ color: quiet ? "var(--ink)" : hue }}>
          {value}
        </div>
      )}
      {sub && <div className="mt-1.5 text-[11px] text-ink-subtle">{sub}</div>}
    </div>
  );
}

export function PanelHeader({
  title, children,
}: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-hairline px-4 h-11">
      <h2 className="text-[13px] font-semibold text-ink">{title}</h2>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}

/** The dashed rule is the one place dashes are allowed: it distinguishes
 *  "nothing here" from "still loading". */
export function EmptyState({
  title, hint, action,
}: { title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="empty-well">
      <div className="text-[13px] font-medium text-ink-muted">{title}</div>
      {hint && (
        <div className="mx-auto mt-1.5 max-w-sm text-[12px] text-ink-subtle">{hint}</div>
      )}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}

/** Height-matched loading rows. A layout that reflows when data lands is
 *  the cheapest-looking thing an interface can do. */
export function SkeletonTable({ rows = 8, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="p-3" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex items-center gap-3 py-1.5">
          {Array.from({ length: cols }).map((_, c) => (
            <div
              key={c}
              className="skeleton"
              style={{
                height: 12,
                // Ragged widths read as text; equal blocks read as a grid.
                width: `${[28, 18, 24, 14, 20][c % 5]}%`,
                opacity: 1 - r * 0.06,
              }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}

/** Confidence rendered as a number plus a hairline bar. The bar is for
 *  scanning a column quickly; the number is for the one row that matters. */
export function Confidence({ value }: { value: number }) {
  const low = value < 0.9;
  return (
    <div className="flex items-center justify-end gap-2">
      <span className="mono text-[12px]"
            style={{ color: low ? "var(--high)" : "var(--ink-muted)" }}>
        {(value * 100).toFixed(1)}
      </span>
      <span className="h-[3px] w-10 shrink-0" style={{ background: "var(--surface-inset)" }}>
        <span
          className="block h-full"
          style={{
            width: `${Math.min(100, value * 100)}%`,
            background: low ? "var(--high)" : "var(--ink-subtle)",
          }}
        />
      </span>
    </div>
  );
}

/** What an operator sees when a request fails.
 *
 *  Deliberately never a stack trace: it states what broke, what it means,
 *  and the one command that fixes the common case. A console that shows a
 *  React error overlay in front of a control room is worse than useless.
 */
export function ErrorState({
  error, onRetry, compact,
}: { error: unknown; onRetry?: () => void; compact?: boolean }) {
  const api = error as { friendly?: string; hint?: string; offline?: boolean;
                         message?: string };
  const offline = Boolean(api?.offline);
  const title = api?.friendly ?? "Something went wrong";
  const hint = api?.hint ?? "";
  const detail = api?.message ?? String(error);

  return (
    <div
      className={compact ? "px-4 py-6" : "empty-well"}
      style={{
        borderColor: offline ? "var(--critical)" : "var(--border-strong)",
        background: offline ? "var(--critical-wash)" : "var(--surface-subtle)",
      }}
      role="alert"
    >
      <div className="text-[13px] font-semibold"
           style={{ color: offline ? "var(--critical)" : "var(--ink)" }}>
        {title}
      </div>
      {hint && (
        <div className="mx-auto mt-2 max-w-md">
          <code className="mono block text-[11px] text-ink-muted">{hint}</code>
        </div>
      )}
      <details className="mx-auto mt-3 max-w-lg text-left">
        <summary className="cursor-pointer text-[11px] text-ink-subtle">
          Technical detail
        </summary>
        <p className="mono mt-1.5 break-words text-[11px] text-ink-subtle">{detail}</p>
      </details>
      {onRetry && (
        <div className="mt-4 flex justify-center">
          <button className="btn" onClick={onRetry}>Retry</button>
        </div>
      )}
    </div>
  );
}
