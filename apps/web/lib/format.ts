/** Formatting helpers. Kept in one place so a timestamp looks identical
 *  on every screen — inconsistent time formats are how operators
 *  misread an incident. */

export function timeOnly(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString("en-GB", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

export function dateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("en-GB", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    second: "2-digit", hour12: false,
  });
}

/** Relative age. Operators scan for "how stale is this", not wall-clock. */
export function ago(iso: string): string {
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 45) return "just now";
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}

export function pct(v: number, digits = 1): string {
  return `${(v * 100).toFixed(digits)}%`;
}

export function metres(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`;
}

export function duration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m < 60 ? `${m}m ${s}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

export const ALERT_LABEL: Record<string, string> = {
  cloned_plate: "Cloned plate",
  speeding: "Speeding",
  loitering: "Loitering",
  watchlist_hit: "Watchlist hit",
  hit_and_run: "Hit and run",
  anomaly: "Anomaly",
  no_plate: "No plate",
  convoy: "Convoy",
};

/** Severity -> the CSS custom properties that render it. Colour is always
 *  accompanied by the text label, never used as the sole signal. */
export const SEVERITY_STYLE: Record<string, { color: string; background: string }> = {
  critical: { color: "var(--critical)", background: "var(--critical-wash)" },
  high:     { color: "var(--high)",     background: "var(--high-wash)" },
  medium:   { color: "var(--medium)",   background: "var(--medium-wash)" },
  low:      { color: "var(--low)",      background: "var(--low-wash)" },
  info:     { color: "var(--info)",     background: "var(--info-wash)" },
};
