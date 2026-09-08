"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useLive } from "@/lib/useLive";
import { SEVERITY_STYLE } from "@/lib/format";

interface Toast {
  id: number;
  severity: string;
  title: string;
  detail: string;
  alertId: number;
}

/** Live alert notifications.
 *
 *  Restrained on purpose: a control room that flashes and slides on every
 *  event trains its operators to ignore it. These appear quietly, stack,
 *  and dismiss themselves — except critical ones, which stay until
 *  someone acts on them.
 */
export function Toasts() {
  const router = useRouter();
  const [toasts, setToasts] = useState<Toast[]>([]);

  useLive((e) => {
    if (e.event !== "alert.created") return;
    const a = e.data;
    const toast: Toast = {
      id: Date.now() + Math.random(),
      severity: a.severity,
      title: a.title,
      detail: a.detail ?? "",
      alertId: a.id,
    };
    setToasts((prev) => [toast, ...prev].slice(0, 4));
  });

  // Auto-dismiss everything except critical, which requires acknowledgement.
  useEffect(() => {
    if (toasts.length === 0) return;
    const timers = toasts
      .filter((t) => t.severity !== "critical")
      .map((t) =>
        setTimeout(
          () => setToasts((prev) => prev.filter((x) => x.id !== t.id)),
          8000
        )
      );
    return () => timers.forEach(clearTimeout);
  }, [toasts]);

  if (toasts.length === 0) return null;

  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-40 flex w-[360px] flex-col gap-2"
      role="status"
      aria-live="polite"
    >
      {toasts.map((t) => {
        const s = SEVERITY_STYLE[t.severity] ?? SEVERITY_STYLE.info;
        return (
          <div
            key={t.id}
            className="pointer-events-auto bg-surface"
            style={{
              border: "1px solid var(--border-strong)",
              borderLeft: `3px solid ${s.color}`,
              boxShadow: "var(--shadow-overlay)",
            }}
          >
            <div className="flex items-start justify-between gap-3 px-3 py-2.5">
              <div className="min-w-0">
                <div className="pill mb-1" style={{ color: s.color, background: s.background }}>
                  {t.severity}
                </div>
                <div className="text-[13px] font-medium text-ink">{t.title}</div>
                <p className="mt-0.5 line-clamp-2 text-[11px] text-ink-muted">{t.detail}</p>
                <button
                  className="mt-2 text-[12px]"
                  style={{ color: "var(--accent)" }}
                  onClick={() => {
                    setToasts((prev) => prev.filter((x) => x.id !== t.id));
                    router.push(`/alerts/${t.alertId}`);
                  }}
                >
                  Open alert
                </button>
              </div>
              <button
                aria-label="Dismiss"
                className="shrink-0 text-[16px] leading-none text-ink-subtle hover:text-ink"
                onClick={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
              >
                ×
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
