"use client";

import { useMemo, useState } from "react";

/** Hourly activity sparkline.
 *
 *  Inline SVG rather than a charting library: it is thirty lines, adds no
 *  bundle weight, and a chart library's defaults would fight the design
 *  system on every axis, gridline and tooltip.
 */
export function Sparkline({
  buckets, height = 56,
}: {
  buckets: { hour: string; n: number }[];
  height?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);

  const { bars, max } = useMemo(() => {
    const max = Math.max(1, ...buckets.map((b) => b.n));
    return { bars: buckets, max };
  }, [buckets]);

  if (buckets.length === 0) {
    return (
      <div className="flex items-center justify-center text-[11px] text-ink-subtle"
           style={{ height }}>
        No activity in this window
      </div>
    );
  }

  const gap = 2;
  const w = 100 / bars.length;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 100 ${height}`}
        preserveAspectRatio="none"
        style={{ width: "100%", height }}
        role="img"
        aria-label={`Sightings per hour, peak ${max}`}
      >
        {bars.map((b, i) => {
          const h = Math.max(1, (b.n / max) * (height - 2));
          const active = hover === i;
          return (
            <rect
              key={b.hour}
              x={i * w + gap / 2}
              y={height - h}
              width={Math.max(0.5, w - gap)}
              height={h}
              fill={active ? "var(--accent)" : "var(--border-strong)"}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            />
          );
        })}
      </svg>
      <div className="mt-1 flex items-center justify-between">
        <span className="mono text-[10px] text-ink-subtle">
          {bars[0]?.hour.slice(11)}:00
        </span>
        <span className="text-[11px] text-ink-muted">
          {hover !== null ? (
            <>
              <span className="mono text-ink">{bars[hover].n}</span>
              <span className="text-ink-subtle"> at {bars[hover].hour.slice(11)}:00</span>
            </>
          ) : (
            <span className="text-ink-subtle">peak {max}/h</span>
          )}
        </span>
        <span className="mono text-[10px] text-ink-subtle">
          {bars[bars.length - 1]?.hour.slice(11)}:00
        </span>
      </div>
    </div>
  );
}
