"use client";

import type React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useLive } from "@/lib/useLive";
import { CommandPalette } from "./CommandPalette";
import { Toasts } from "./Toasts";
import {
  IconAlert, IconAudit, IconBack, IconCamera, IconChevron, IconFace,
  IconForward, IconLive, IconMap, IconOverview, IconPanel, IconReview,
  IconSearch, IconWatchlist,
} from "./icons";

/** Navigation, grouped by what an operator is trying to do.
 *
 *  Labels are words, not emoji — an emoji in a police command centre
 *  reads as a toy. `key` is the second stroke of a vim-style "g then x".
 */
const GROUPS: {
  title: string | null;
  items: { href: string; label: string; key: string; icon: () => React.JSX.Element }[];
}[] = [
  {
    title: null,
    items: [{ href: "/dashboard", label: "Overview", key: "d", icon: IconOverview }],
  },
  {
    title: "Monitor",
    items: [
      { href: "/live",   label: "Live feed", key: "l", icon: IconLive },
      { href: "/map",    label: "Live map",  key: "m", icon: IconMap },
      { href: "/alerts", label: "Alerts",    key: "a", icon: IconAlert },
    ],
  },
  {
    title: "Investigate",
    items: [
      { href: "/search",  label: "Vehicle search", key: "s", icon: IconSearch },
      { href: "/scan",    label: "Face scan",      key: "f", icon: IconFace },
      { href: "/persons", label: "Watchlist",      key: "w", icon: IconWatchlist },
    ],
  },
  {
    title: "Manage",
    items: [
      { href: "/review",  label: "Review queue", key: "r", icon: IconReview },
      { href: "/cameras", label: "Cameras",      key: "c", icon: IconCamera },
      { href: "/audit",   label: "Audit log",    key: "u", icon: IconAudit },
    ],
  },
];

const ALL = GROUPS.flatMap((g) => g.items);
const DENSITIES = ["compact", "default", "wall"] as const;

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [openAlerts, setOpenAlerts] = useState<number | null>(null);
  const [pendingReview, setPendingReview] = useState<number | null>(null);
  const [density, setDensity] = useState<(typeof DENSITIES)[number]>("default");
  const [collapsed, setCollapsed] = useState(false);
  const [recents, setRecents] = useState<string[]>([]);
  const [showRecents, setShowRecents] = useState(true);

  // Restore the operator's own layout choices.
  useEffect(() => {
    const d = (localStorage.getItem("netra-density") ?? "default") as
      (typeof DENSITIES)[number];
    setDensity(d);
    document.documentElement.dataset.density = d;
    setCollapsed(localStorage.getItem("netra-rail") === "1");
    try {
      setRecents(JSON.parse(localStorage.getItem("netra-recents") ?? "[]"));
    } catch { /* corrupt entry is not worth crashing the shell over */ }
  }, []);

  // Track visited screens so an investigator can step back to where they
  // were, the way the Cloudflare console does.
  useEffect(() => {
    setRecents((prev) => {
      const next = [pathname, ...prev.filter((p) => p !== pathname)].slice(0, 5);
      localStorage.setItem("netra-recents", JSON.stringify(next));
      return next;
    });
  }, [pathname]);

  const cycleDensity = () => {
    const next = DENSITIES[(DENSITIES.indexOf(density) + 1) % DENSITIES.length];
    setDensity(next);
    document.documentElement.dataset.density = next;
    localStorage.setItem("netra-density", next);
  };

  const toggleRail = () => {
    setCollapsed((c) => {
      localStorage.setItem("netra-rail", c ? "0" : "1");
      return !c;
    });
  };

  useEffect(() => {
    let armed = false;
    let timer: ReturnType<typeof setTimeout>;
    const onKey = (e: KeyboardEvent) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(
        (e.target as HTMLElement)?.tagName);
      if (typing || e.ctrlKey || e.metaKey) return;
      // Alt+Left / Alt+Right mirror the browser's own history keys.
      if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); router.back(); return; }
      if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); router.forward(); return; }
      if (e.altKey) return;
      if (e.key === "g") {
        armed = true;
        clearTimeout(timer);
        timer = setTimeout(() => { armed = false; }, 1200);
        return;
      }
      if (armed) {
        const t = ALL.find((n) => n.key === e.key.toLowerCase());
        if (t) { e.preventDefault(); router.push(t.href); }
        armed = false;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); clearTimeout(timer); };
  }, [router]);

  const connected = useLive((e) => {
    if (e.event === "alert.created") setOpenAlerts((n) => (n ?? 0) + 1);
    if (e.event === "review.queued") setPendingReview((n) => (n ?? 0) + 1);
    if (e.event === "review.decided") setPendingReview((n) => Math.max(0, (n ?? 1) - 1));
  });

  useEffect(() => {
    fetch("/api/v1/stats").then((r) => r.json())
      .then((s) => { setOpenAlerts(s.alerts_open); setPendingReview(s.review_pending); })
      .catch(() => {});
  }, [pathname]);

  const badgeFor = (href: string) =>
    href === "/alerts" ? openAlerts : href === "/review" ? pendingReview : null;

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: "var(--canvas)" }}>
      <aside
        className="flex shrink-0 flex-col border-r border-hairline bg-surface transition-[width] duration-150"
        style={{ width: collapsed ? 60 : "var(--sidebar-w)" }}
      >
        {/* ---- brand ---- */}
        <div className="flex h-header items-center gap-2.5 border-b border-hairline px-4">
          <svg viewBox="0 0 64 64" width="24" height="24" aria-hidden
               className="shrink-0" style={{ color: "var(--accent)" }}>
            <circle cx="32" cy="32" r="25.6" fill="none" stroke="currentColor" strokeWidth="2.6" />
            <path d="M32 13.4 L48.1 22.7 L48.1 41.3 L32 50.6 L15.9 41.3 L15.9 22.7 Z"
                  fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinejoin="round" />
            <circle cx="32" cy="32" r="9.6" fill="none" stroke="currentColor" strokeWidth="2.6" />
            <circle cx="32" cy="32" r="4" fill="currentColor" />
          </svg>
          {!collapsed && (
            <div className="min-w-0">
              <div className="text-[15px] font-semibold leading-none tracking-tight text-ink">
                NETRA
              </div>
              <div className="label-micro mt-0.5 truncate">ANPR Command Centre</div>
            </div>
          )}
        </div>

        {/* ---- quick search ---- */}
        <div className="px-2 pt-2">
          <button
            onClick={() => window.dispatchEvent(new CustomEvent("netra:palette"))}
            className="flex w-full items-center gap-2 rounded-sm border border-hairline-strong
                       bg-surface-subtle px-2 py-1.5 text-left transition-colors hover:bg-surface-sunken"
            title="Quick search (Ctrl K)"
          >
            <span className="shrink-0 text-ink-subtle"><IconSearch /></span>
            {!collapsed && (
              <>
                <span className="flex-1 text-[12px] text-ink-subtle">Quick search…</span>
                <kbd className="kbd shrink-0">Ctrl K</kbd>
              </>
            )}
          </button>
        </div>

        {/* ---- recents ---- */}
        {!collapsed && recents.length > 1 && (
          <div className="px-2 pt-3">
            <button onClick={() => setShowRecents((v) => !v)}
                    className="flex w-full items-center gap-1 px-2 py-1 text-ink-subtle hover:text-ink">
              <IconChevron open={showRecents} />
              <span className="label-micro">Recents</span>
            </button>
            {showRecents && (
              <div className="mt-0.5">
                {recents.filter((r) => r !== pathname).slice(0, 3).map((r) => {
                  const item = ALL.find((n) => r.startsWith(n.href));
                  return (
                    <Link key={r} href={r}
                          className="block truncate rounded-sm px-2 py-1 pl-7 text-[12px]
                                     text-ink-muted hover:bg-surface-subtle hover:text-ink">
                      {item?.label ?? r}
                    </Link>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* ---- grouped nav ---- */}
        <nav className="flex-1 overflow-y-auto px-2 py-3">
          {GROUPS.map((group, gi) => (
            <div key={group.title ?? `g${gi}`} className={gi ? "mt-4" : ""}>
              {group.title && !collapsed && (
                <div className="label-micro px-2 pb-1">{group.title}</div>
              )}
              {group.title && collapsed && (
                <div className="mx-2 mb-2 border-t border-hairline" />
              )}
              {group.items.map((item) => {
                const active = pathname.startsWith(item.href);
                const badge = badgeFor(item.href);
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    title={collapsed ? item.label : undefined}
                    className="group relative flex items-center gap-2.5 rounded-sm px-2 py-[7px]
                               text-[13px] transition-colors"
                    style={{
                      color: active ? "var(--accent)" : "var(--ink-muted)",
                      fontWeight: active ? 600 : 400,
                      background: active ? "var(--accent-wash)" : "transparent",
                    }}
                  >
                    <span className="shrink-0"><Icon /></span>
                    {!collapsed && <span className="flex-1 truncate">{item.label}</span>}
                    {badge ? (
                      <span
                        className="mono shrink-0 rounded-sm px-1.5 py-px text-[10px]"
                        style={{
                          color: item.href === "/alerts"
                            ? "var(--critical)" : "var(--ink-muted)",
                          background: item.href === "/alerts"
                            ? "var(--critical-wash)" : "var(--surface-sunken)",
                          ...(collapsed
                            ? { position: "absolute", top: 2, right: 2, padding: "0 3px" }
                            : {}),
                        }}
                      >
                        {badge}
                      </span>
                    ) : null}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        {/* ---- footer ---- */}
        <div className="border-t border-hairline px-2 py-2">
          {!collapsed && (
            <button onClick={cycleDensity}
                    className="flex w-full items-center justify-between rounded-sm px-2 py-1
                               text-[11px] text-ink-subtle hover:bg-surface-subtle hover:text-ink"
                    title="Cycle display density">
              <span>Density</span><span className="mono">{density}</span>
            </button>
          )}
          <div className="flex items-center gap-2 px-2 py-1.5">
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${connected ? "dot-live" : ""}`}
                  style={{ background: connected ? "var(--ok)" : "var(--ink-subtle)" }}
                  title={connected ? "Live feed connected" : "Reconnecting"} />
            {!collapsed && (
              <span className="truncate text-[11px] text-ink-subtle">
                {connected ? "Live feed connected" : "Reconnecting…"}
              </span>
            )}
          </div>
          <button onClick={toggleRail}
                  className="flex w-full items-center gap-2.5 rounded-sm px-2 py-1.5
                             text-ink-subtle hover:bg-surface-subtle hover:text-ink"
                  title={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            <span className="shrink-0"><IconPanel /></span>
            {!collapsed && <span className="text-[11px]">Collapse</span>}
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto">{children}</main>

      <CommandPalette />
      <Toasts />
    </div>
  );
}

/** Page header with history controls.
 *
 *  The console is a single-page app, so the browser chrome is not always
 *  visible (kiosk, wall display, full screen). Back has to live in the
 *  interface itself or an investigator three screens deep is stranded.
 */
export function PageHeader({
  title, description, children,
}: { title: string; description?: string; children?: React.ReactNode }) {
  const router = useRouter();
  const [canBack, setCanBack] = useState(false);

  useEffect(() => {
    // history.length > 1 means there is somewhere to go back to. It is a
    // coarse signal, but the browser gives us nothing better, and a Back
    // button that silently does nothing is worse than a disabled one.
    setCanBack(typeof window !== "undefined" && window.history.length > 1);
  }, []);

  return (
    <header className="sticky top-0 z-10 flex h-header items-center justify-between gap-4
                       border-b border-hairline bg-surface px-6">
      <div className="flex min-w-0 items-center gap-3">
        <div className="flex shrink-0 items-center">
          <button onClick={() => router.back()} disabled={!canBack}
                  className="btn btn-ghost h-7 w-7 justify-center px-0"
                  title="Back (Alt+←)" aria-label="Go back">
            <IconBack />
          </button>
          <button onClick={() => router.forward()}
                  className="btn btn-ghost h-7 w-7 justify-center px-0"
                  title="Forward (Alt+→)" aria-label="Go forward">
            <IconForward />
          </button>
        </div>
        <div className="min-w-0">
          <h1 className="truncate text-[15px] font-semibold tracking-tight text-ink">
            {title}
          </h1>
          {description && (
            <p className="truncate text-[11px] leading-tight text-ink-subtle">
              {description}
            </p>
          )}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">{children}</div>
    </header>
  );
}
