"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

/** Ctrl/Cmd-K command palette.
 *
 *  An operator working an incident should never hunt through a menu. Type
 *  a partial plate and hit Enter. Everything else in the console is
 *  reachable from the same box.
 */

interface Item {
  id: string;
  label: string;
  hint?: string;
  group: "Plate" | "Go to" | "Action";
  run: () => void;
  mono?: boolean;
}

const PAGES: { label: string; href: string; keys: string }[] = [
  { label: "Overview", href: "/dashboard", keys: "dashboard home stats" },
  { label: "Live map", href: "/map", keys: "map geo incidents" },
  { label: "Live feed", href: "/live", keys: "live camera video anpr" },
  { label: "Alerts", href: "/alerts", keys: "alerts incidents triage" },
  { label: "Search", href: "/search", keys: "search vehicle plate" },
  { label: "Face scan", href: "/scan", keys: "face scan person recognise" },
  { label: "Person watchlist", href: "/persons", keys: "watchlist enrol person wanted missing" },
  { label: "Review queue", href: "/review", keys: "review queue human" },
  { label: "Cameras", href: "/cameras", keys: "cameras network status" },
  { label: "Audit log", href: "/audit", keys: "audit accountability log" },
];

export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [plates, setPlates] = useState<{ plate: string; sighting_count: number }[]>([]);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // --- global hotkeys ---
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(
        (e.target as HTMLElement)?.tagName
      );
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      } else if (e.key === "/" && !typing) {
        e.preventDefault();
        setOpen(true);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    // The sidebar's quick-search button opens the same palette rather
    // than duplicating a second search UI.
    const openIt = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("netra:palette", openIt);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("netra:palette", openIt);
    };
  }, []);

  useEffect(() => {
    if (open) {
      setQ("");
      setActive(0);
      setTimeout(() => inputRef.current?.focus(), 10);
    }
  }, [open]);

  // --- live plate lookup, debounced ---
  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) {
      setPlates([]);
      return;
    }
    const t = setTimeout(() => {
      fetch(`/api/vehicles/search?q=${encodeURIComponent(term)}&reason=command palette`)
        .then((r) => r.json())
        .then((d) => setPlates((d.results ?? []).slice(0, 6)))
        .catch(() => setPlates([]));
    }, 160);
    return () => clearTimeout(t);
  }, [q]);

  const items: Item[] = useMemo(() => {
    const term = q.trim().toLowerCase();
    const out: Item[] = [];

    for (const p of plates) {
      out.push({
        id: `plate-${p.plate}`,
        label: p.plate,
        hint: `${p.sighting_count} sightings`,
        group: "Plate",
        mono: true,
        run: () => router.push(`/vehicle/${p.plate}`),
      });
    }

    for (const page of PAGES) {
      if (!term || page.label.toLowerCase().includes(term) || page.keys.includes(term)) {
        out.push({
          id: `page-${page.href}`,
          label: page.label,
          group: "Go to",
          run: () => router.push(page.href),
        });
      }
    }

    if (term.length >= 2) {
      out.push({
        id: "action-search",
        label: `Search all vehicles for "${q.trim().toUpperCase()}"`,
        group: "Action",
        run: () => router.push(`/search?q=${encodeURIComponent(q.trim())}`),
      });
    }
    return out;
  }, [q, plates, router]);

  useEffect(() => setActive(0), [items.length]);

  const choose = useCallback((item?: Item) => {
    if (!item) return;
    setOpen(false);
    item.run();
  }, []);

  if (!open) return null;

  const grouped = items.reduce<Record<string, Item[]>>((acc, i) => {
    (acc[i.group] ??= []).push(i);
    return acc;
  }, {});
  let flatIndex = -1;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[12vh]"
      style={{ background: "rgb(9 9 11 / 0.28)" }}
      onClick={() => setOpen(false)}
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <div
        className="w-full max-w-xl overflow-hidden bg-surface"
        style={{ border: "1px solid var(--border-strong)", boxShadow: "var(--shadow-overlay)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-hairline px-4">
          <span className="label-micro shrink-0">Search</span>
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setActive((a) => Math.min(a + 1, items.length - 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setActive((a) => Math.max(a - 1, 0));
              } else if (e.key === "Enter") {
                e.preventDefault();
                choose(items[active]);
              }
            }}
            placeholder="Plate number, or a page name…"
            className="h-12 w-full bg-transparent text-[15px] text-ink outline-none placeholder:text-ink-subtle"
          />
          <kbd className="mono shrink-0 text-[10px] text-ink-subtle">ESC</kbd>
        </div>

        <div className="max-h-[380px] overflow-y-auto py-1">
          {items.length === 0 ? (
            <div className="px-4 py-6 text-center text-[12px] text-ink-subtle">
              {q.trim().length < 2 ? "Type at least two characters" : "No matches"}
            </div>
          ) : (
            Object.entries(grouped).map(([group, list]) => (
              <div key={group}>
                <div className="label-micro px-4 pb-1 pt-2">{group}</div>
                {list.map((item) => {
                  flatIndex += 1;
                  const isActive = flatIndex === active;
                  const myIndex = flatIndex;
                  return (
                    <button
                      key={item.id}
                      onMouseEnter={() => setActive(myIndex)}
                      onClick={() => choose(item)}
                      className="flex w-full items-center justify-between px-4 py-2 text-left"
                      style={{ background: isActive ? "var(--surface-sunken)" : "transparent" }}
                    >
                      <span className={`text-[13px] text-ink ${item.mono ? "plate" : ""}`}>
                        {item.label}
                      </span>
                      {item.hint && (
                        <span className="mono text-[11px] text-ink-subtle">{item.hint}</span>
                      )}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>

        <div className="flex items-center gap-4 border-t border-hairline px-4 py-2">
          {[["↑↓", "navigate"], ["↵", "open"], ["esc", "close"]].map(([k, v]) => (
            <span key={k} className="flex items-center gap-1.5">
              <kbd className="mono text-[10px] text-ink-muted">{k}</kbd>
              <span className="text-[10px] text-ink-subtle">{v}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
