<!-- status: DRAFT (recovered from interrupted run; not yet adversarially hardened) -->

## Frontend — Command Centre Dashboard

### 0. Stack pins, workspace layout, dependency budget

Verified against the npm registry on 2026-09-05 from the dev machine (Node 24.16.0, npm 11.13.0):

| Package | Pin | Why |
|---|---|---|
| `next` | `16.3.4` | App Router; `params`/`searchParams` are **Promises** in this major — every page that reads them is `async` |
| `react` / `react-dom` | `19.2.8` | required by Next 16 |
| `tailwindcss` | `4.3.3` | CSS-first config (`@theme`), no `tailwind.config.js` needed |
| `mapbox-gl` | `3.30.0` | used **raw** (no `react-map-gl`) so we can call `source.setData()` imperatively |
| `@supabase/supabase-js` | `2.115.0` | Realtime + PostgREST |
| `@supabase/ssr` | `0.12.6` | cookie-based auth in RSC + middleware |
| `@tanstack/react-query` | `5.102.8` | client cache, hydration, mutation for ack/correct |
| `@tanstack/react-virtual` | `3.14.10` | virtualised sighting/alert lists |
| `lucide-react` | `1.41.0` | icons, **per-icon deep imports only** |

Deliberately **not** installed: any component library, any charting library, `date-fns`/`moment` (use `Intl.DateTimeFormat`), `turf` (we need ~15 lines of interpolation, not 400 kB), `react-map-gl`. Budget target: **first-load JS ≤ 180 kB gzip on every route except `/map`**; `/map` is allowed ~400 kB because `mapbox-gl` alone is ~230 kB gzip and is dynamically imported so it never touches other routes.

```
apps/web/
├── middleware.ts                      # session refresh + RBAC route gate
├── app/
│   ├── layout.tsx                     # <html data-density data-scale>, fonts, providers
│   ├── globals.css                    # @theme tokens (section 1)
│   ├── (auth)/
│   │   ├── layout.tsx                 # centred single-column, no app shell
│   │   └── login/page.tsx
│   └── (console)/
│       ├── layout.tsx                 # AppShell: 56px left rail + 48px topbar + AlertTicker
│       ├── dashboard/page.tsx
│       ├── map/page.tsx
│       ├── search/page.tsx
│       ├── vehicle/[plate]/page.tsx
│       ├── vehicle/[plate]/loading.tsx
│       ├── alerts/page.tsx            # table + drawer driven by ?open=<alert_id>
│       ├── alerts/[id]/page.tsx       # full-page fallback for deep links / printing
│       ├── cameras/page.tsx           # live wall
│       ├── review/page.tsx            # human-in-the-loop OCR queue
│       └── admin/page.tsx
├── components/
│   ├── shell/{AppShell,Rail,TopBar,AlertTicker,DensityToggle}.tsx
│   ├── primitives/{Button,Input,Badge,SeverityBadge,Kbd,Sheet,Skeleton,EmptyState,ErrorState}.tsx
│   ├── table/{DataTable,VirtualRows,ColumnHeader,BulkBar}.tsx
│   ├── map/{MapCanvas,mapStyle.ts,layers.ts,useTrajectory.ts,Scrubber.tsx}.tsx
│   ├── alerts/{AlertTable,AlertDrawer,EvidencePlayer}.tsx
│   ├── review/{ReviewCard,CandidateList,CharConfidenceStrip}.tsx
│   └── cameras/{LiveTile,LiveWall}.tsx
├── lib/
│   ├── supabase/{client.ts,server.ts,realtime.ts}
│   ├── api.ts                         # typed fetch wrapper over FastAPI
│   ├── plate.ts                       # normalise/format/validate Bharat plates
│   ├── geo.ts                         # haversine, lerp-along-track
│   └── types.db.ts                    # generated: supabase gen types typescript
└── .env.local
```

Env vars (exact names): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_MAPBOX_TOKEN`, `NEXT_PUBLIC_STORAGE_BASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (server-only, never `NEXT_PUBLIC_`).

---

### 1. The design law, encoded as tokens

Everything is a CSS variable in `globals.css`. Tailwind v4 reads them via `@theme`, and any arbitrary value (`text-[var(--ink)]`) also works, so the tokens survive a Tailwind downgrade.

```css
/* app/globals.css */
@import "tailwindcss";

@theme {
  --color-surface:     #FFFFFF;
  --color-surface-2:   #FAFAFB;   /* table zebra, rail */
  --color-line:        #E3E5E9;   /* every border is 1px of this */
  --color-line-strong: #C7CAD1;
  --color-ink:         #111318;   /* 18.4:1 on white */
  --color-ink-2:       #4A4F58;   /* secondary, 8.6:1 */
  --color-ink-3:       #767C86;   /* micro-labels only, never body text */
  --color-accent:      #0B4F9E;   /* THE single accent: focus, links, selection */
  --color-accent-soft: #EAF1F9;

  /* semantic — permitted ONLY on severity affordances */
  --color-s1: #B3261E;  --color-s2: #A15C00;
  --color-s3: #40566B;  --color-s4: #6B7280;
  --color-ok: #1B5E20;  /* camera online dot only */

  --font-sans: "Inter", ui-sans-serif, system-ui;
  --font-mono: "JetBrains Mono", ui-monospace, monospace; /* plates, IDs, timestamps */
}

:root { --row-h: 34px; --pad-x: 12px; font-size: 15px; }
html[data-density="compact"]    { --row-h: 28px; --pad-x: 8px;  font-size: 14px; }
html[data-density="wall"]       { --row-h: 44px; --pad-x: 16px; font-size: 19px; }
@media (min-width: 2200px) { :root { font-size: 17px; } }

* { box-shadow: none !important; }          /* enforce the no-shadow law */
.hairline { border: 1px solid var(--color-line); }
.num { font-variant-numeric: tabular-nums lining-nums; font-family: var(--font-mono); }
.micro { font-size: 0.6875rem; letter-spacing: .08em; text-transform: uppercase;
         color: var(--color-ink-3); font-weight: 500; }
:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 1px; }
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
```

`data-density` is persisted in `localStorage` **and** mirrored into a cookie so the RSC `<html>` renders with the right value and there is no density flash. `wall` is the mode for the 55-inch control-room display: 19px root, 44px rows, ~7 m legibility.

**Severity is never colour alone.** `<SeverityBadge>` renders a 10×10 SVG square whose fill fraction encodes level (full / three-quarter / half / hollow), a mono code `S1`–`S4`, and a `title`/`aria-label` with the word. Colour is the third redundant channel, so protanopes and a monochrome projector both read it.

```tsx
const SEV = {
  1: { code: "S1", word: "Critical", c: "var(--color-s1)", fill: 1 },
  2: { code: "S2", word: "High",     c: "var(--color-s2)", fill: 0.75 },
  3: { code: "S3", word: "Medium",   c: "var(--color-s3)", fill: 0.5 },
  4: { code: "S4", word: "Low",      c: "var(--color-s4)", fill: 0 },
} as const;

export function SeverityBadge({ level }: { level: 1 | 2 | 3 | 4 }) {
  const s = SEV[level];
  return (
    <span className="inline-flex items-center gap-1.5" aria-label={`Severity ${s.word}`}>
      <svg width="10" height="10" aria-hidden="true">
        <rect x="0.5" y="0.5" width="9" height="9" fill="none" stroke={s.c} />
        <rect x="0.5" y={0.5 + 9 * (1 - s.fill)} width="9" height={9 * s.fill} fill={s.c} />
      </svg>
      <span className="num text-[11px]" style={{ color: s.c }}>{s.code}</span>
    </span>
  );
}
```

---

### 2. Data-fetching architecture

Three layers, each with one job.

1. **RSC = first paint, always.** Every list page is an `async` Server Component that queries Supabase with the user's cookie session and renders real rows into HTML. No spinner is ever the first thing an operator sees.
2. **TanStack Query = the client-side owner of that same list**, seeded from RSC so it does not refetch on mount.
3. **Supabase Realtime = deltas only**, merged into the Query cache with `setQueryData`. Realtime never *is* the list; it *edits* the list.

```tsx
// app/(console)/alerts/page.tsx  — Server Component
import { createClient } from "@/lib/supabase/server";
import AlertsClient from "./AlertsClient";

export const dynamic = "force-dynamic";

export default async function AlertsPage({
  searchParams,
}: { searchParams: Promise<{ status?: string; sev?: string; open?: string }> }) {
  const sp = await searchParams;                       // Next 16: Promise
  const supabase = await createClient();
  const statuses = (sp.status ?? "new,ack").split(",");

  const { data, error } = await supabase
    .from("alerts")
    .select(`id, alert_type, severity, status, plate_text_normalized, camera_id,
             occurred_at, confidence, evidence_crop_path,
             cameras!alerts_camera_id_fkey ( camera_code, name )`)
    .in("status", statuses)
    .order("occurred_at", { ascending: false })
    .limit(100);

  if (error) throw new Error(error.message);           // caught by error.tsx
  return <AlertsClient initialRows={data ?? []} serverTime={new Date().toISOString()}
                       openId={sp.open ?? null} />;
}
```

```tsx
// AlertsClient.tsx  — "use client"
const qc = useQueryClient();
const { data: rows } = useQuery({
  queryKey: ["alerts", filterKey],
  queryFn: () => api.get<AlertRow[]>(`/api/v1/alerts?${filterKey}`),
  initialData: initialRows,
  initialDataUpdatedAt: Date.parse(serverTime),  // <- kills the mount refetch
  staleTime: 60_000,
  refetchOnWindowFocus: false,
});
```

`initialDataUpdatedAt` is the single line that removes the **flash of stale data**: without it React Query treats server data as infinitely old, refetches on mount, and the table visibly re-sorts ~300 ms after paint. With it, the server rows are considered fresh for `staleTime` and the only mutation source is Realtime.

**Realtime, without re-render storms.** In the demo, 8 replayed cameras produce up to ~40 `plate_reads` INSERTs/second. One `setState` per event = 40 React commits/second on a 16 GB laptop that is *also* running eight ONNX inference workers. So every subscription writes into a ref buffer and flushes on a 500 ms interval — below the ~1 s threshold at which humans stop calling a UI "live", and a 20× reduction in commits.

```ts
// lib/supabase/realtime.ts
export function useBufferedChannel<T>(
  channelName: string,
  binds: { event: "INSERT" | "UPDATE" | "*"; table: string; filter?: string }[],
  onFlush: (batch: { event: string; row: T }[]) => void,
  { flushMs = 500, onDesync }: { flushMs?: number; onDesync?: () => void } = {},
) {
  const buf = useRef<{ event: string; row: T }[]>([]);
  const cb = useRef(onFlush); cb.current = onFlush;

  useEffect(() => {
    const sb = createBrowserClient();
    let ch = sb.channel(channelName);
    for (const b of binds) {
      ch = ch.on("postgres_changes",
        { event: b.event, schema: "public", table: b.table, filter: b.filter },
        (p) => { buf.current.push({ event: p.eventType, row: p.new as T }); });
    }
    ch.subscribe((status) => {
      if (status === "SUBSCRIBED") onDesync?.();          // gap-fill on (re)connect
      if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") {
        setTimeout(() => ch.subscribe(), 2000);           // reconnect, then gap-fill
      }
    });

    const t = setInterval(() => {
      if (!buf.current.length) return;
      const batch = buf.current; buf.current = [];
      cb.current(batch);
    }, flushMs);

    return () => { clearInterval(t); sb.removeChannel(ch); };
  }, [channelName, JSON.stringify(binds)]);   // eslint-disable-line
}
```

Two non-obvious Realtime facts the team must design around:

- **Realtime payloads contain only the changed row's own columns — never joined data.** An `alerts` INSERT gives you `camera_id`, not `camera_code`. Load `cameras` once into a `Map<uuid, Camera>` in a context provider at app shell mount and enrich client-side.
- **Disconnects lose events silently.** On every `SUBSCRIBED` transition, run a gap-fill: `GET /api/v1/alerts?since=<lastSeenOccurredAt>` and merge by `id`. That is what `onDesync` is for.

> **Verify:** the exact `filter` operator set for `postgres_changes` (`eq/neq/lt/lte/gt/gte/in`) and whether `in` is available on your Supabase version. If `severity=lte.2` is rejected, subscribe unfiltered and drop rows client-side. Also confirm the DB section has run `ALTER PUBLICATION supabase_realtime ADD TABLE public.alerts, public.plate_reads, public.camera_status;` — without it the client subscribes successfully and receives nothing, which is the single most common two-hour bug in this stack.

**Mutations** (`ack`, `dismiss`, `submit correction`) go through the FastAPI layer, never a direct client write, so audit rows are written server-side. Each uses optimistic `onMutate` + rollback; the subsequent Realtime UPDATE is idempotent because merges are keyed by `id`.

---

### 3. Screens

#### 3.1 `/(auth)/login`
**Purpose:** Supabase email+password sign-in; establishes the cookie session that RSC and middleware read.
**Layout:** Full-viewport white. A single 360 px column centred at 42 % viewport height. Top: a 20 px monochrome emblem, then `SENTINEL — CITY ANPR COMMAND` in 13 px uppercase tracked micro-label, then a hairline rule. Two stacked fields (`Official email`, `Password`), 34 px tall, 1 px border, no rounding beyond 2 px. Full-width accent button `SIGN IN`. Below the rule, 11 px ink-3 text: "Access is logged. Processing under DPDP Act 2023 — official law-enforcement purpose only."
**Data:** none pre-auth. `POST` via server action → `supabase.auth.signInWithPassword`.
**States:** *loading* — button label swaps to `SIGNING IN…`, fields disabled, no spinner. *error* — a 1 px `--color-s1` left rule block above the button with the literal message ("Invalid credentials", "Account disabled"), field borders turn `--color-s1`; never a toast. *empty* — n/a.
`middleware.ts` refreshes the session on every request and redirects unauthenticated users to `/login?next=<path>`; it also enforces role gates: `/review` requires role `reviewer|admin`, `/admin` requires `admin`, from `public.profiles.role`.

#### 3.2 `/dashboard` — live overview
**Purpose:** the wall-mounted default view; "is the city healthy, and what just happened?"
**Layout:** 12-column grid, 24 px gutters, 24 px page padding.
- Row 1: four KPI cells in a hairline-bordered strip, each `micro` label above a 30 px tabular number — `PLATE READS / 60 MIN`, `ACTIVE ALERTS`, `CAMERAS ONLINE (n/m)`, `MEDIAN OCR CONFIDENCE`. Under each number a 60×16 px inline-SVG sparkline in `--color-ink-3` (hand-rolled `<polyline>`, no chart library).
- Row 2, columns 1–7: **Live alert feed**, newest-first, max 50 rows, each row = `SeverityBadge · HH:MM:SS · ALERT TYPE · PLATE (mono) · CAM-07 Ring Rd`. New rows insert at top with a 700 ms `background: var(--color-accent-soft)` fade (disabled under `prefers-reduced-motion`).
- Row 2, columns 8–12: **Mini map** (non-interactive, `dragPan:false`), showing camera dots and the last 30 minutes of alerts.
- Row 3, full width: **Recent sightings** table, 12 rows, columns `TIME · PLATE · CAMERA · CLASS · CONF · CROP` (28 px thumbnail).
**Data:** RSC calls `GET /api/v1/stats/overview` (KPIs + sparkline arrays), `alerts` limit 50, `plate_reads` limit 12. Then Realtime on `alerts` (INSERT/UPDATE) and `plate_reads` (INSERT).
**States:** *loading* — RSC `loading.tsx` renders the exact same grid with `--color-surface-2` skeleton blocks at identical heights, so there is zero layout shift. *empty* — "No plate reads in the last 60 minutes." plus a secondary line "Check worker status on /cameras". *error* — `error.tsx` boundary per grid cell (each cell wrapped in its own `<Suspense>` + `<ErrorBoundary>`), so a failed KPI query does not blank the alert feed.

#### 3.3 `/map` — the city
**Purpose:** spatial situational awareness and the destination of the hero interaction. Detailed in section 4.
**Layout:** Map fills the viewport minus the shell. A 300 px right control panel, white, hairline left border, holding: a plate search field, date-range inputs, alert-type checkboxes, layer toggles (`Cameras`, `Trajectory`, `Incident heat`), and — when a trajectory is loaded — the ordered stop list. A bottom-left 240 px legend card. A bottom-centre scrubber bar, only mounted when a trajectory exists.
**Data:** `GET /api/v1/cameras` (RSC, cached 5 min), `GET /api/v1/incidents/heatmap?bbox=&from=&to=`, `GET /api/v1/vehicles/{plate}/trajectory?from=&to=`.
**States:** *loading* — map renders immediately (base tiles), overlays arrive later; the stop list shows 6 skeleton rows. *empty* — "No sightings for MH12DE1433 in this window." with a "Widen to 24 h" button. *error* — if the Mapbox token is missing/rejected, replace the canvas with a bordered panel: "Map unavailable — MAPBOX_TOKEN rejected" and a link to the same data as a table, so the demo degrades instead of dying.

#### 3.4 `/search` — plate search
**Purpose:** the entry point of the hero flow.
**Layout:** Centred 720 px column. A 44 px input in mono, 18 px, `placeholder="MH12DE1433"`, with an inline formatted echo below in ink-3 (`MH 12 DE 1433`) confirming normalisation. Under it a row of filter chips: `LAST 1 H | 6 H | 24 H | 7 D | CUSTOM`, and a camera multi-select. Results are a dense table: `PLATE · SIGHTINGS · FIRST SEEN · LAST SEEN · CAMERAS · ALERTS`. Fuzzy matches appear in a second block under a `micro` heading `NEAR MATCHES (OCR-CONFUSABLE)` with the differing characters underlined.
**Data:** `GET /api/v1/plates/search?q=&from=&to=&camera_ids=&fuzzy=true`. Input debounced **250 ms**, minimum **3 characters** — an Indian plate's first three characters (`MH1`) already narrow to a district, so shorter queries only produce full-table scans.
**States:** *loading* — the results block keeps previous rows at `opacity:.5` with a 2 px accent progress line under the input (`placeholderData: keepPreviousData`), never a blank. *empty* — "No vehicle matches MH12DE1433. Try a partial plate, or `?` for an unknown character." *error* — inline bordered error strip with a `RETRY` text button.

#### 3.5 `/vehicle/[plate]`
**Purpose:** everything the system knows about one vehicle.
**Layout:** Header band: plate in 28 px mono with a `WATCHLIST` toggle, then a 4-cell inline stat row (`FIRST SEEN`, `LAST SEEN`, `SIGHTINGS`, `DISTINCT CAMERAS`). Below, a two-pane split: left 60 % is the trajectory map (same `MapCanvas` component); right 40 % is a **virtualised** sighting list, one row per read: `#seq · HH:MM:SS · CAM-12 · conf 0.97` with a 48×24 px plate crop. Hovering a row highlights the corresponding map stop (`map.setFeatureState`) and vice-versa. A tab strip above the right pane switches `SIGHTINGS | ALERTS | CO-TRAVELLERS (stretch)`.
**Data:** `GET /api/v1/vehicles/{plate}/sightings?cursor=&limit=200` (cursor-paginated, `useInfiniteQuery`), `GET /api/v1/vehicles/{plate}/trajectory`, `GET /api/v1/alerts?plate=`.
**States:** *loading* — `loading.tsx` shows header skeleton + 12 row skeletons at 34 px. *empty* — "Plate recognised in the registry but never sighted." *error* — full-pane error with the request id.

#### 3.6 `/alerts` — triage table
Detailed in section 6.

#### 3.7 `/cameras` — the live wall
Detailed in section 7.

#### 3.8 `/review` — human-in-the-loop
Detailed in section 8.

#### 3.9 `/admin`
**Purpose:** operational transparency; also where judges look for "is this a real system?".
**Layout:** Left sub-nav (`Cameras`, `Users & Roles`, `Alert Rules`, `Model Registry`, `Audit Log`, `Data Retention`) with a single content column.
- *Cameras*: CRUD table — `camera_code`, `name`, `rtsp_url` (masked), `lat`, `lon`, `heading_deg`, `status`, `last_heartbeat_at`.
- *Alert Rules*: editable numeric thresholds (speed limit km/h, clone-implausibility km/h, loitering dwell seconds, minimum OCR confidence to auto-alert) — these must be visible and tweakable **on stage**.
- *Model Registry*: read-only rows of `model_name`, `version`, `onnx_path`, `exported_at`, `val_accuracy`, `execution_provider` (`DmlExecutionProvider`).
- *Audit Log*: virtualised, append-only `actor · action · target · at`.
- *Data Retention*: the DPDP Act 2023 panel — retention window in days, purge job status, and a `PURGE NOW` button behind a typed confirmation.
**States:** *loading* skeleton rows; *empty* per-section copy; *error* inline. All writes are `admin`-role-gated server actions.

---

### 4. The map, in detail

**Mount.** Mapbox GL touches `window` at import time, so it must never be server-rendered.

```tsx
// components/map/MapCanvas.tsx  — "use client"
const MapCanvas = dynamic(() => import("./MapCanvasImpl"), {
  ssr: false,
  loading: () => <div className="h-full w-full bg-[var(--color-surface-2)] hairline" />,
});
```

Inside `MapCanvasImpl`, the `mapboxgl.Map` lives in a `useRef` and is created exactly once. **Layers are added once, on `style.load`, with empty `FeatureCollection`s; every subsequent data change is `(map.getSource(id) as GeoJSONSource).setData(fc)`.** No React state ever re-creates a layer — that is the entire "memoised map layers" strategy, and it is why we skipped `react-map-gl`.

**Restrained monochrome base.** Start from `mapbox://styles/mapbox/light-v11`, then strip it at runtime so the only saturated pixels on screen are severity colours.

```ts
// components/map/mapStyle.ts
export function muteBasemap(map: mapboxgl.Map) {
  for (const layer of map.getStyle()?.layers ?? []) {
    const id = layer.id;
    if (/poi|transit|airport|natural-label|water-point/.test(id)) {
      map.setLayoutProperty(id, "visibility", "none"); continue;
    }
    if (layer.type === "symbol") {
      map.setPaintProperty(id, "text-color", "#767C86");
      map.setPaintProperty(id, "text-halo-color", "#FFFFFF");
      map.setPaintProperty(id, "text-halo-width", 1.2);
    } else if (layer.type === "fill") {
      map.setPaintProperty(id, "fill-color", /water/.test(id) ? "#EDEFF2" : "#F7F8F9");
    } else if (layer.type === "line") {
      map.setPaintProperty(id, "line-color", "#E3E5E9");
    } else if (layer.type === "background") {
      map.setPaintProperty(id, "background-color", "#FFFFFF");
    }
  }
}
```

> **Verify:** layer-id naming inside `light-v11` (`poi-label`, `road-*`, `water`) — if a regex misses, the visual result is a slightly-too-colourful road, not a crash. As a **stretch**, bake the same rules into a saved Studio style and set `NEXT_PUBLIC_MAPBOX_STYLE_URL` to skip the runtime loop entirely.

**Cameras source** — `FeatureCollection<Point>`:

```json
{ "type": "FeatureCollection", "features": [
  { "type": "Feature", "id": 7,
    "geometry": { "type": "Point", "coordinates": [73.85674, 18.52043] },
    "properties": { "camera_id": "9f1c…", "camera_code": "CAM-07",
                    "name": "Ring Rd / Sector 12", "status": "online",
                    "heading_deg": 135, "reads_last_5m": 84 } } ] }
```

```ts
map.addLayer({ id: "cameras-dot", type: "circle", source: "cameras",
  paint: {
    "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 3.5, 16, 7],
    "circle-color": "#FFFFFF",
    "circle-stroke-width": 2,
    "circle-stroke-color": ["match", ["get", "status"],
      "online", "#1B5E20", "degraded", "#A15C00", /* offline */ "#C7CAD1"],
  }});
// status is ALSO encoded by shape: offline cameras additionally get a hollow cross icon layer
map.addLayer({ id: "cameras-label", type: "symbol", source: "cameras", minzoom: 13,
  layout: { "text-field": ["get", "camera_code"], "text-size": 10,
            "text-offset": [0, 1.1], "text-anchor": "top" },
  paint: { "text-color": "#4A4F58", "text-halo-color": "#fff", "text-halo-width": 1.2 }});
```

**Trajectory source** — one `FeatureCollection` containing both numbered stops and legs, so a single `setData` updates the whole path:

```json
{ "type": "FeatureCollection",
  "features": [
    { "type": "Feature", "id": 1,
      "geometry": { "type": "Point", "coordinates": [73.8567, 18.5204] },
      "properties": { "kind": "stop", "seq": 1, "plate_read_id": 481203,
                      "camera_code": "CAM-03", "captured_at": "2026-09-05T11:04:12Z",
                      "epoch_ms": 1788606252000, "plate_confidence": 0.972,
                      "crop_url": "https://…/crops/481203.jpg" } },
    { "type": "Feature", "id": 1001,
      "geometry": { "type": "LineString",
                    "coordinates": [[73.8567,18.5204],[73.8821,18.5310]] },
      "properties": { "kind": "leg", "from_seq": 1, "to_seq": 2,
                      "seconds": 312, "distance_m": 4120,
                      "speed_kmph": 47.5, "implausible": false } }
  ] }
```

```ts
map.addLayer({ id: "traj-leg", type: "line", source: "trajectory",
  filter: ["==", ["get", "kind"], "leg"],
  layout: { "line-cap": "round", "line-join": "round" },
  paint: {
    "line-width": 2.5,
    "line-color": ["case", ["get", "implausible"], "#B3261E", "#0B4F9E"],
    "line-dasharray": ["case", ["get", "implausible"], ["literal", [2, 1.5]], ["literal", [1, 0]]],
  }});

map.addLayer({ id: "traj-arrow", type: "symbol", source: "trajectory",
  filter: ["==", ["get", "kind"], "leg"],
  layout: { "symbol-placement": "line", "symbol-spacing": 90,
            "icon-image": "arrow-sdf", "icon-size": 0.6,
            "icon-rotation-alignment": "map", "icon-allow-overlap": true },
  paint: { "icon-color": "#0B4F9E", "icon-halo-color": "#fff", "icon-halo-width": 1 }});

map.addLayer({ id: "traj-stop", type: "circle", source: "trajectory",
  filter: ["==", ["get", "kind"], "stop"],
  paint: { "circle-radius": 11, "circle-color": "#FFFFFF",
           "circle-stroke-width": 1.5, "circle-stroke-color": "#111318" }});

map.addLayer({ id: "traj-stop-num", type: "symbol", source: "trajectory",
  filter: ["==", ["get", "kind"], "stop"],
  layout: { "text-field": ["to-string", ["get", "seq"]], "text-size": 11,
            "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"] },
  paint: { "text-color": "#111318" }});
```

The `arrow-sdf` image is registered once with `map.addImage("arrow-sdf", img, { sdf: true })` from a 16×16 triangle PNG in `/public`. Using an SDF icon rather than a `▶` `text-field` avoids the "glyph missing from the font set" failure mode entirely.

**Time scrubber.** A `<input type="range">` over the trajectory's epoch window, plus `1× / 8× / 60×` speed buttons and a play/pause key (`Space`). Position is interpolated linearly in time between consecutive stops — at city scale (≤ 30 km) lon/lat lerp error is under a metre, so no `turf` dependency:

```ts
// components/map/Scrubber.tsx (core)
function positionAt(stops: { epoch: number; lngLat: [number, number] }[], t: number) {
  if (!stops.length) return null;
  if (t <= stops[0].epoch) return stops[0].lngLat;
  for (let i = 1; i < stops.length; i++) {
    const a = stops[i - 1], b = stops[i];
    if (t <= b.epoch) {
      const f = (t - a.epoch) / Math.max(1, b.epoch - a.epoch);
      return [a.lngLat[0] + (b.lngLat[0] - a.lngLat[0]) * f,
              a.lngLat[1] + (b.lngLat[1] - a.lngLat[1]) * f] as [number, number];
    }
  }
  return stops[stops.length - 1].lngLat;
}

// rAF loop writes straight to the source — no React state per frame
useEffect(() => {
  if (!playing) return;
  let raf = 0, last = performance.now();
  const step = (now: number) => {
    tRef.current += (now - last) * speedRef.current; last = now;
    const p = positionAt(stops, tRef.current);
    if (p) (map.getSource("ghost") as GeoJSONSource).setData(
      { type: "Feature", geometry: { type: "Point", coordinates: p }, properties: {} } as never);
    map.setFilter("traj-stop", ["all", ["==", ["get","kind"],"stop"],
                                ["<=", ["get","epoch_ms"], tRef.current]]);
    setLabel(fmtClock(tRef.current));            // one setState per frame, cheap text only
    raf = requestAnimationFrame(step);
  };
  raf = requestAnimationFrame(step);
  return () => cancelAnimationFrame(raf);
}, [playing, stops, map]);
```

Under `prefers-reduced-motion`, the play button becomes `STEP` and advances stop-by-stop instead of animating.

**Incident heat.** One unclustered source `incidents` of weighted points; a `heatmap` layer with `maxzoom: 13`, and individual `circle` pins from `minzoom: 13` — a clustered source cannot correctly feed a heatmap layer, so do not try to share one.

```json
{ "type": "Feature", "geometry": { "type": "Point", "coordinates": [73.86, 18.52] },
  "properties": { "alert_id": "…", "severity": 1, "alert_type": "cloned_plate", "weight": 1.0 } }
```

```ts
map.addLayer({ id: "incident-heat", type: "heatmap", source: "incidents", maxzoom: 13,
  paint: {
    "heatmap-weight": ["interpolate", ["linear"], ["get", "severity"], 1, 1, 4, 0.25],
    "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 8, 0.6, 13, 2],
    "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 8, 12, 13, 32],
    "heatmap-opacity": ["interpolate", ["linear"], ["zoom"], 11, 0.75, 13, 0],
    "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"],
      0, "rgba(255,255,255,0)", 0.2, "#E7DCD8", 0.5, "#D9A79E", 0.8, "#C05C50", 1, "#B3261E"],
  }});
```

Because the base map is grey, this single warm ramp is the only heat on screen and reads instantly from across a room. Clustered count bubbles are **stretch**.

---

### 5. Hero interaction: plate → trajectory (the 90-second demo)

1. Operator lands on `/search` (or hits `/` from anywhere — a global hotkey focuses the plate field).
2. Types `mh12de1433`. `lib/plate.ts` `normalisePlate()` uppercases and strips separators; the input echoes `MH 12 DE 1433` beneath in ink-3 so the operator sees the canonical form.
3. At 3+ chars, 250 ms debounce, `GET /api/v1/plates/search?q=MH12DE1433&from=…&to=…&fuzzy=true`. Exact hits render above `NEAR MATCHES (OCR-CONFUSABLE)`; near matches are generated by the backend's confusion set (`0↔O`, `1↔I`, `8↔B`, `5↔S`, `2↔Z`, `6↔G`) and the differing character is underlined.
4. Click (or `Enter` on the focused row) → `router.push('/vehicle/MH12DE1433')`. **The RSC for that route fetches trajectory + sightings server-side**, so the map and the ordered stop list appear in the same paint.
5. `MapCanvas` receives the trajectory FeatureCollection, calls `setData`, then `map.fitBounds(bbox, { padding: 64, duration: prefersReducedMotion ? 0 : 900 })`. Stops are numbered 1..n in chronological order; directional arrows run along each leg.
6. Any leg whose implied speed exceeds the clone threshold renders dashed red and its stop-list rows carry an `S1 IMPLAUSIBLE — 214 km/h` badge. This is where the cloned-plate story lands.
7. Operator presses `Space`; the ghost marker drives the path at 60×, stops reveal progressively, and the clock label counts real timestamps.
8. Clicking any stop opens a 320 px right sheet with the plate crop, full frame, camera name, exact timestamp, OCR confidence, and two buttons: `OPEN CAMERA` → `/cameras?focus=CAM-07`, `SEND TO REVIEW` → enqueues the read.

Every one of those steps is a URL change, so the whole demo is back-button-safe and rehearsable.

---

### 6. `/alerts` — triage table and detail drawer

**Columns** (left to right, all `micro` uppercase headers, hairline separators, no vertical rules):

| Col | Width | Content |
|---|---|---|
| checkbox | 32 px | bulk selection, `Space` toggles |
| SEV | 64 px | `<SeverityBadge>`; row also gets a 3 px left rule in the severity colour |
| TIME | 92 px | `HH:MM:SS` mono, `title` = full ISO; a second line shows `+2m` relative |
| TYPE | 150 px | `CLONED_PLATE`, `OVERSPEED`, `HIT_AND_RUN`, `LOITERING`, `FIGHT`, `ACCIDENT`, `WEAPON`, `WATCHLIST_HIT` |
| PLATE | 130 px | mono, links to `/vehicle/[plate]` |
| CAMERA | 180 px | `CAM-07 · Ring Rd / Sector 12` |
| CONF | 64 px | `0.94` tabular, right-aligned |
| STATUS | 90 px | `NEW` / `ACK` / `RESOLVED` / `DISMISSED` as an outlined chip |
| EVIDENCE | 56 px | 40×22 crop thumbnail, or a film icon if a clip exists |

**Sort/filter.** Default sort `occurred_at DESC`; clicking a header cycles asc → desc → default and writes `?sort=occurred_at.desc` into the URL. Filters (status, severity, type, camera, time range, plate) are all URL search params, so the RSC re-runs and every filtered view is shareable and bookmarkable — important because a judge may ask "show me only critical".

**Keyboard.** The table is the focus owner: `<tbody>` uses roving `tabIndex`. `j`/`↓` next row, `k`/`↑` previous, `Enter` open drawer, `Esc` close, `a` acknowledge focused row, `x` toggle selection, `Shift+x` range-select from anchor, `A` acknowledge all selected, `g g` top, `G` bottom, `?` shortcut sheet. Shortcuts are suppressed while an `<input>` has focus.

**Bulk acknowledge.** Selecting ≥1 row slides in a bottom bar (hairline top border, white): `12 SELECTED · ACKNOWLEDGE · DISMISS · CLEAR`. `POST /api/v1/alerts/bulk-ack` with `{ ids: string[], note?: string }`. Optimistic: rows immediately show `ACK` and a 6-second `UNDO` appears in the bar; on error, rollback and show the failed ids inline.

**Detail drawer.** Opening a row sets `?open=<alert_id>` (via `router.replace`, `scroll:false`) so the table state survives. The drawer is a 480 px right sheet with a hairline left border and no shadow:
- Header: severity badge, type, `occurred_at`, and status chip.
- **Evidence**: the plate crop at 2× (`image-rendering: pixelated` so operators see real pixels, not a smoothed lie), then the full annotated frame, then — if `evidence_clip_path` is present — a `<video controls preload="metadata">` pointing at a 6-second MP4 in the `evidence` bucket. MP4 over HLS: a 6-second clip does not need segmenting, and `<video>` plays it with zero JS.
- **Context map**: a 200 px-tall `MapCanvas` in `static` mode centred on the alert camera, showing the two legs before and after.
- **Reasoning block**: renders `alerts.payload` jsonb as a definition list — e.g. `distance_m 4120 · seconds 69 · implied_speed_kmph 214 · threshold_kmph 150`. Judges reward a system that shows its arithmetic.
- **Actions**: `ACKNOWLEDGE`, `ESCALATE`, `DISMISS (reason required)`, `OPEN VEHICLE`, `COPY ALERT ID`.

**States.** *loading*: the drawer opens instantly with header data already in the row cache and skeletons only for evidence. *empty* (no alerts match): centred hairline panel, "No alerts match these filters", with a `CLEAR FILTERS` text button. *error*: the table keeps the last good rows and shows a top strip "Live connection lost — showing data from 11:42:07. Retrying…" — never an empty screen.

---

### 7. `/cameras` — the live wall

**Recommendation for the hackathon: polled annotated JPEG, not MJPEG and not HLS.** Reasoning: HLS via MediaMTX adds a packaging hop and 6–10 s of latency, which *undermines* the live claim rather than supporting it; MJPEG holds one HTTP connection per tile open and will fight the 8 ONNX-DirectML worker processes for the same 16 GB of RAM. The worker already decodes and annotates every Nth frame — writing that frame to `frames/{camera_code}/latest.jpg` in the Storage bucket costs it nothing extra, and the browser fetching a 50–70 kB JPEG once per second per tile is ~0.5 MB/s for a 9-tile wall. HLS on the focused tile only is the **stretch**.

```tsx
// components/cameras/LiveTile.tsx — "use client"
export function LiveTile({ code, focused }: { code: string; focused: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [src, setSrc] = useState<string>();
  const [stale, setStale] = useState(false);
  const visible = useIsIntersecting(ref);            // IntersectionObserver, 10% threshold

  useEffect(() => {
    if (!visible || document.hidden) return;
    const period = focused ? 1000 : 3000;            // focused 1 Hz, background 0.33 Hz
    let cancelled = false;
    const tick = () => {
      const url = `${process.env.NEXT_PUBLIC_STORAGE_BASE_URL}/frames/${code}/latest.jpg?v=${Date.now()}`;
      const img = new Image();                       // preload, then swap: no white flash
      img.onload  = () => { if (!cancelled) { setSrc(url); setStale(false); } };
      img.onerror = () => { if (!cancelled) setStale(true); };
      img.src = url;
    };
    tick();
    const id = setInterval(tick, period);
    return () => { cancelled = true; clearInterval(id); };
  }, [code, focused, visible]);

  return (
    <div ref={ref} className="hairline bg-[var(--color-surface-2)] aspect-video relative">
      {src ? <img src={src} alt={`Live view, camera ${code}`} className="w-full h-full object-cover" />
           : <div className="w-full h-full" />}
      <div className="absolute left-0 bottom-0 right-0 flex justify-between
                      bg-white/95 px-2 py-1 micro border-t border-[var(--color-line)]">
        <span>{code}</span>
        <span className="num">{stale ? "NO SIGNAL" : "LIVE"}</span>
      </div>
    </div>
  );
}
```

**Layout:** a 3×3 grid at 1080p, 4×3 at 1440p+, 12 px gaps, each tile 16:9 with a white caption bar carrying `CAM-07 · Ring Rd · 24 fps · 84 reads/5m` and a 6 px status dot **plus** the word `LIVE` / `DEGRADED` / `NO SIGNAL`. Clicking a tile promotes it to a 2×2 span and raises its poll rate to 1 Hz; `Esc` restores. A right-hand 260 px column lists all cameras with status, last heartbeat, and a `LOCATE` link to `/map?camera=CAM-07`.
**Data:** `GET /api/v1/cameras` (RSC) + Realtime on `camera_status` for the dots. Freshness is judged client-side: if `Date.now() - last_heartbeat_at > 15000` the tile is `NO SIGNAL` regardless of what the row says — 15 s is 3 missed 5-second heartbeats, long enough to survive a GC pause on this laptop.
**States:** *loading* — grey tile with `CONNECTING` micro-label. *empty* — "No cameras registered. Add one in /admin." *error* — the tile keeps the last good frame at 60 % opacity with a `NO SIGNAL` caption and a `t+18s` staleness counter, which is far more useful in a control room than a blank rectangle.

---

### 8. `/review` — human-in-the-loop OCR correction

This screen is the differentiator: it proves the team knows a 97 %-accurate model is a 3 %-wrong model, and it manufactures training data live on stage.

**Layout.** Single-purpose, no distractions. A 1000 px centred column.
- Top strip: `QUEUE 128 REMAINING · 42 CORRECTED THIS SESSION · MODEL AGREEMENT 88.3% · AVG 3.1 s/ITEM`.
- Left 460 px: the **plate crop** rendered at native pixels scaled 4× with `image-rendering: pixelated`, on a `--color-surface-2` field with a hairline border. Below it, a 120 px thumbnail of the full frame with the plate bbox drawn as a 1 px accent rectangle; clicking it opens the full frame.
- Right 500 px:
  - The **input**: 32 px mono, uppercase-forced, pre-filled with the model's top candidate, text selected on mount so typing replaces it.
  - Under the input, the **character-confidence strip**: one 34 px-wide cell per character, showing the character above and its confidence below in 10 px mono. Cells with `char_confidence < 0.80` get a 2 px bottom rule in `--color-s2` and a `title` listing the runner-up character. This directly visualises `plate_reads.char_confidences`.
  - **Confusable hint** row: if the top candidate contains any of `0O 1I 8B 5S 2Z 6G`, show a chip: `AMBIGUOUS: O vs 0 at position 5`.
  - **Candidate list**: up to 5 beam candidates as rows `[1] MH12DE1433  0.941` … `[5] MH12DE1A33  0.032`, with a 1 px horizontal confidence bar in ink-3. Pressing the digit selects that candidate into the input.
  - **Format validator**: live-checks against the Bharat/RTO regexes and shows `VALID — RTO FORMAT`, `VALID — BH SERIES`, or `NON-STANDARD` (never blocks submission; vanity and damaged plates exist).

```ts
// lib/plate.ts
export const RTO_RE = /^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}$/;   // MH12DE1433, DL8CAF5030, HR26DK8337
export const BH_RE  = /^\d{2}BH\d{4}[A-Z]{1,2}$/;           // 22BH1234AA
export const normalisePlate = (s: string) =>
  s.toUpperCase().replace(/[^A-Z0-9]/g, "");
export const formatPlate = (p: string) =>
  p.replace(/^([A-Z]{2})(\d{1,2})([A-Z]{0,3})(\d{4})$/, "$1 $2 $3 $4").replace(/\s+/g, " ").trim();
```

**Keyboard-first flow.** The reviewer never touches the mouse.

| Key | Action |
|---|---|
| `Enter` | Submit the input as the truth, advance to next item |
| `1`–`5` | Load candidate *n* into the input (does not submit) |
| `Tab` | Accept top candidate verbatim and submit |
| `U` | Mark `UNREADABLE` (blur/occlusion) and advance |
| `N` | Mark `NOT_A_PLATE` (false detection) and advance |
| `[` / `]` | Previous / next without deciding |
| `Ctrl+Z` | Undo the last submission (5-second window, optimistic revert) |
| `Z` | Toggle 8× magnifier on the crop |

```tsx
// components/review/ReviewCard.tsx (handler core)
const onKeyDown = (e: React.KeyboardEvent) => {
  if (e.key === "Enter") { e.preventDefault(); submit("corrected", normalisePlate(value)); }
  else if (e.key === "Tab") { e.preventDefault(); submit("confirmed", candidates[0].text); }
  else if (/^[1-5]$/.test(e.key) && e.altKey) { setValue(candidates[+e.key - 1]?.text ?? value); }
  else if (e.key.toLowerCase() === "u" && e.ctrlKey) { submit("unreadable", null); }
  else if (e.key.toLowerCase() === "n" && e.ctrlKey) { submit("not_a_plate", null); }
};
// submit() calls the mutation, then advances the index; the NEXT item's crop
// was already <link rel="prefetch">-ed when the current one mounted, so the
// perceived latency between items is ~0 ms.
```

**Queue mechanics.** `GET /api/v1/review/next?limit=25` returns a batch already ordered by triage value: (a) reads whose `plate_confidence` is in the uncertainty band `0.55–0.90` — below 0.55 the crop is usually unreadable and wastes human time, above 0.90 the model is right ~99 % of the time; (b) reads that produced an S1/S2 alert regardless of confidence; (c) reads whose plate is one edit-distance from a watchlist plate. The client keeps the whole batch in memory and prefetches the next batch when 5 items remain, so the reviewer never waits.

**Submission and the training feedback loop.**

```http
POST /api/v1/review/{plate_read_id}
{ "decision": "corrected", "corrected_text": "MH12DE1433",
  "original_text": "MH12DE1A33", "time_spent_ms": 2840, "reviewer_id": "…" }
```

The backend writes a `plate_review` row, updates `plate_reads.plate_text_normalized` and `review_status`, and re-runs alert matching for that read (a correction can *create* a watchlist hit — demo that). The accumulated corrections are the dataset: `GET /api/v1/review/export?format=paddleocr&since=…` streams a zip containing `crops/{plate_read_id}.jpg` and a `rec_gt.txt` of tab-separated `crops/481203.jpg\tMH12DE1433` lines, which is exactly PaddleOCR's recognition label format and drops straight into a Colab fine-tune notebook; the retrained model is exported to ONNX and dropped back into the worker. Surface that loop **in the UI**: an `/admin → Model Registry` line reading `plate_rec_v2.onnx · trained on 1,284 human corrections · val 0.981 · DmlExecutionProvider`. A closed loop that a judge can see is worth more than one percentage point of accuracy.
**States:** *loading* — crop area skeleton, input disabled; *empty* — "Queue clear. 0 reads awaiting review." with the session stats and a `REFRESH` button; *error* — the item stays on screen, a strip reads "Submission failed — retrying (2/3)", and submissions queue locally so a network blip never loses a reviewer's work.

---

### 9. Performance

- **Virtualise anything that can exceed 200 rows**: `/vehicle/[plate]` sightings, `/alerts`, `/admin → Audit Log`. `useVirtualizer({ count, getScrollElement, estimateSize: () => 34, overscan: 12 })` with a fixed row height read from `--row-h` so estimation is exact and there is no scroll-jitter. Render `<tbody>` with a spacer row above and below rather than absolute positioning, to keep semantic table markup for screen readers.
- **Never re-render the map.** `MapCanvasImpl` has no props that change per frame; all updates go through `setData` / `setFilter` / `setFeatureState`. Hover highlighting uses `setFeatureState`, not a filter rebuild.
- **Realtime batching** (section 2): 500 ms flush, hard caps of 50 rows in the ticker and 500 rows in a table page — a delta stream must never be allowed to grow the DOM without bound. Anything beyond the cap is dropped from the client list, not from the database.
- **List identity**: every row is keyed by database `id` (never array index), and row components are `React.memo` with a comparator on `updated_at`, so a batch of 40 inserts re-renders 40 new rows and zero existing ones.
- **Images**: crops are served from Supabase Storage with `loading="lazy"`, explicit `width`/`height` to reserve space, and a Storage transform (`?width=96`) for thumbnails so the wall is not downloading 1080p frames for 40 px images.
- **Bundle**: `mapbox-gl` only via `dynamic(..., {ssr:false})`; `lucide-react` icons imported individually (`import Search from "lucide-react/dist/esm/icons/search"`); no barrel `index.ts` re-exports in `components/` (they defeat tree-shaking); dates formatted with a module-level `Intl.DateTimeFormat` singleton (constructing one per row costs ~40 µs and it shows at 500 rows). Run `ANALYZE=1 next build` once before submission and fail the review if any non-map route exceeds 200 kB first-load JS.

---

### 10. Accessibility and the control-room reality

- **Contrast**: body ink `#111318` on white is 18.4:1; secondary `#4A4F58` is 8.6:1; `--color-ink-3` is reserved for uppercase micro-labels at ≥11 px only. Every severity colour clears 4.5:1 on white.
- **Never colour alone**: severity carries glyph + code + colour; camera status carries dot + word; implausible trajectory legs carry a dash pattern + red. The whole UI is legible in greyscale — test by adding `filter: grayscale(1)` to `<html>` in devtools before the demo.
- **Density and scale**: `data-density="comfortable | compact | wall"` toggled from the topbar and persisted in a cookie; `wall` is a documented pre-demo step so the projected UI reads from the back of the room.
- **Keyboard**: every screen is operable without a mouse; a `?` overlay lists shortcuts per screen; focus rings are 2 px accent and never removed; the drawer traps focus and restores it to the originating row on `Esc`.
- **Announcements**: an `aria-live="polite"` region announces only S1 alerts (`Critical alert, cloned plate, MH 12 DE 1433, camera 7`) — announcing every insert would be unusable at 40 events/second.
- **Motion**: `prefers-reduced-motion` disables the map `flyTo` (use `jumpTo`), the new-row fade, and the scrubber animation.
- **Semantics**: real `<table>/<th scope="col">` markup, `<button>` for actions (never a clickable `<div>`), `alt` text on every crop naming camera and time, and the plate rendered in mono with `aria-label={formatPlate(p)}` so screen readers say "M H 1 2 D E 1 4 3 3" as groups rather than a word.

---

## Appendix — Interface Contracts Declared by This Section

- `route: /login (group (auth)) — Supabase email+password sign-in`
- `route: /dashboard — live overview (RSC + Realtime)`
- `route: /map — city map, trajectory, heatmap, scrubber`
- `route: /search — plate search entry point`
- `route: /vehicle/[plate] — vehicle profile + trajectory + sightings`
- `route: /alerts — triage table; drawer opened via ?open=<alert_id>`
- `route: /alerts/[id] — full-page alert fallback for deep links`
- `route: /cameras — live wall; ?focus=CAM-07 promotes a tile`
- `route: /review — human-in-the-loop OCR correction queue`
- `route: /admin — cameras, users, alert rules, model registry, audit log, retention`
- `file: apps/web/ is the Next.js workspace root of the monorepo`
- `file: apps/web/app/globals.css — all design tokens as CSS variables under @theme`
- `file: apps/web/lib/plate.ts — normalisePlate(), formatPlate(), RTO_RE, BH_RE`
- `file: apps/web/lib/supabase/{client.ts,server.ts,realtime.ts}`
- `file: apps/web/lib/types.db.ts — output of `supabase gen types typescript``
- `file: apps/web/middleware.ts — session refresh + role gate (/review: reviewer|admin, /admin: admin)`
- `env: NEXT_PUBLIC_SUPABASE_URL`
- `env: NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `env: NEXT_PUBLIC_API_BASE_URL (FastAPI origin, e.g. http://127.0.0.1:8000)`
- `env: NEXT_PUBLIC_MAPBOX_TOKEN`
- `env: NEXT_PUBLIC_MAPBOX_STYLE_URL (optional, stretch: pre-muted Studio style)`
- `env: NEXT_PUBLIC_STORAGE_BASE_URL (Supabase Storage public base, e.g. https://<ref>.supabase.co/storage/v1/object/public)`
- `env: SUPABASE_SERVICE_ROLE_KEY (server-only, never NEXT_PUBLIC_)`
- `endpoint: GET /api/v1/stats/overview -> {reads_60m, active_alerts, cameras_online, cameras_total, median_ocr_confidence, sparklines:{reads:number[], alerts:number[]}}`
- `endpoint: GET /api/v1/plates/search?q=&from=&to=&camera_ids=&fuzzy=true -> [{plate_text_normalized, sightings, first_seen_at, last_seen_at, camera_count, alert_count, match_type:'exact'|'fuzzy', diff_positions:number[]}]`
- `endpoint: GET /api/v1/vehicles/{plate}/trajectory?from=&to= -> GeoJSON FeatureCollection of kind='stop' Points and kind='leg' LineStrings`
- `endpoint: GET /api/v1/vehicles/{plate}/sightings?cursor=&limit=200 -> {items:[...], next_cursor:string|null}`
- `endpoint: GET /api/v1/alerts?status=&severity=&type=&camera_id=&plate=&from=&to=&since=&sort=&cursor=&limit= -> {items:[...], next_cursor}`
- `endpoint: GET /api/v1/alerts/{id} -> full alert with payload jsonb, evidence_crop_url, evidence_clip_url, context legs`
- `endpoint: POST /api/v1/alerts/{id}/ack  body {note?:string}`
- `endpoint: POST /api/v1/alerts/bulk-ack  body {ids:string[], note?:string} -> {acked:string[], failed:string[]}`
- `endpoint: POST /api/v1/alerts/{id}/dismiss  body {reason:string}`
- `endpoint: GET /api/v1/cameras -> [{camera_id, camera_code, name, lat, lon, heading_deg, status, last_heartbeat_at, reads_last_5m}]`
- `endpoint: GET /api/v1/incidents/heatmap?bbox=&from=&to=&types= -> GeoJSON FeatureCollection of Points with properties {alert_id, severity, alert_type, weight}`
- `endpoint: GET /api/v1/review/next?limit=25 -> [{plate_read_id, crop_url, frame_url, bbox, camera_code, captured_at, plate_confidence, char_confidences:number[], candidates:[{text, score}]}]`
- `endpoint: POST /api/v1/review/{plate_read_id} body {decision:'corrected'|'confirmed'|'unreadable'|'not_a_plate', corrected_text:string|null, original_text:string, time_spent_ms:number}`
- `endpoint: GET /api/v1/review/export?format=paddleocr&since= -> zip containing crops/{plate_read_id}.jpg and rec_gt.txt lines '<path>\t<PLATE>'`
- `endpoint: GET /api/v1/review/stats -> {remaining, corrected_today, model_agreement_rate, avg_seconds_per_item}`
- `storage: bucket 'frames', object path frames/{camera_code}/latest.jpg overwritten by the edge worker (public read)`
- `storage: bucket 'crops', object path crops/{plate_read_id}.jpg`
- `storage: bucket 'evidence', object path evidence/{alert_id}.mp4 (6-second H.264 MP4, NOT HLS)`
- `table consumed: alerts(id uuid, alert_type text, severity smallint 1..4, status text in ('new','ack','resolved','dismissed'), plate_text_normalized text, camera_id uuid, occurred_at timestamptz, detected_at timestamptz, confidence real, payload jsonb, evidence_crop_path text, evidence_clip_path text, ack_by uuid, ack_at timestamptz)`
- `table consumed: plate_reads(id bigint, track_id uuid, camera_id uuid, plate_text_normalized text, plate_confidence real, char_confidences real[], captured_at timestamptz, crop_path text, frame_path text, bbox int[], vehicle_class text, review_status text)`
- `table consumed: cameras(id uuid, camera_code text unique, name text, lat double precision, lon double precision, heading_deg smallint, status text in ('online','degraded','offline'), last_heartbeat_at timestamptz, rtsp_url text)`
- `table consumed: camera_status(camera_id uuid, status text, last_heartbeat_at timestamptz, fps real, reads_last_5m int)`
- `table consumed: plate_review(id uuid, plate_read_id bigint, decision text, original_text text, corrected_text text, reviewer_id uuid, time_spent_ms int, created_at timestamptz)`
- `table consumed: profiles(id uuid = auth.users.id, full_name text, role text in ('viewer','operator','reviewer','admin'))`
- `table consumed: vehicles(plate_text_normalized text pk, first_seen_at, last_seen_at, sightings_count int, watchlist boolean)`
- `realtime: publication supabase_realtime MUST include public.alerts, public.plate_reads, public.camera_status`
- `realtime channel: 'alerts-stream' — postgres_changes INSERT+UPDATE on public.alerts`
- `realtime channel: 'reads-stream' — postgres_changes INSERT on public.plate_reads`
- `realtime channel: 'camera-status' — postgres_changes UPDATE on public.camera_status`
- `fk name relied on by PostgREST embed: alerts_camera_id_fkey (alerts.camera_id -> cameras.id)`
- `GeoJSON contract: trajectory stop properties {kind:'stop', seq, plate_read_id, camera_code, captured_at, epoch_ms, plate_confidence, crop_url}`
- `GeoJSON contract: trajectory leg properties {kind:'leg', from_seq, to_seq, seconds, distance_m, speed_kmph, implausible:boolean}`
- `GeoJSON contract: camera point properties {camera_id, camera_code, name, status, heading_deg, reads_last_5m}`
- `mapbox source ids: 'cameras', 'trajectory', 'incidents', 'ghost'`
- `mapbox layer ids: cameras-dot, cameras-label, traj-leg, traj-arrow, traj-stop, traj-stop-num, incident-heat, incident-pin, ghost-dot`
- `mapbox image id: 'arrow-sdf' registered from /public/arrow.png with {sdf:true}`
- `design token names: --color-surface, --color-surface-2, --color-line, --color-line-strong, --color-ink, --color-ink-2, --color-ink-3, --color-accent (#0B4F9E), --color-accent-soft, --color-s1..--color-s4, --color-ok, --row-h, --pad-x`
- `html attribute: data-density in {comfortable, compact, wall}, mirrored to a cookie named 'density' for flash-free RSC render`
- `threshold: Realtime flush interval 500 ms`
- `threshold: plate search debounce 250 ms, minimum 3 characters`
- `threshold: live tile poll 1000 ms focused / 3000 ms background; NO SIGNAL after 15000 ms without heartbeat`
- `threshold: review queue uncertainty band plate_confidence in [0.55, 0.90]; char-confidence warning below 0.80`
- `threshold: client list caps — 50 rows in ticker, 500 rows per table page, virtualise above 200 rows`
- `threshold: first-load JS budget 180 kB gzip per route (400 kB allowed on /map)`
- `alert_type vocabulary rendered by the UI: cloned_plate, overspeed, hit_and_run, loitering, fight, accident, weapon, watchlist_hit`
- `severity encoding: integer 1..4 rendered as S1 Critical, S2 High, S3 Medium, S4 Low`

## Appendix — MVP vs Stretch

- MVP: /login with Supabase email+password, cookie session, middleware role gate
- MVP: app shell (rail + topbar + alert ticker) and the full token set in globals.css
- MVP: /dashboard with 4 KPI cells, live alert feed, recent sightings table
- MVP: /search with debounced plate search, normalisation echo, exact + fuzzy result blocks
- MVP: /vehicle/[plate] with trajectory map + virtualised sighting list + hover linking
- MVP: /map with muted monochrome basemap, camera layer, trajectory layer (stops + numbers + arrows), incident heatmap
- MVP: time scrubber with play/pause and 1x/8x/60x speeds
- MVP: /alerts triage table with URL-driven sort/filter, keyboard navigation (j/k/Enter/a/x), bulk acknowledge, detail drawer with crop + clip + context map + payload reasoning block
- MVP: /cameras live wall using polled latest.jpg (1 Hz focused / 0.33 Hz background) with preload-then-swap and NO SIGNAL staleness
- MVP: /review queue — crop at 4x, candidate list, char-confidence strip, keyboard-first submit, prefetch of next item, session stats
- MVP: RSC-first data fetching with TanStack Query initialData + initialDataUpdatedAt to kill the mount refetch
- MVP: buffered Realtime channel (500 ms flush) with reconnect gap-fill via ?since=
- MVP: SeverityBadge with glyph + code + colour (never colour alone) and greyscale-legible UI
- MVP: density toggle including 'wall' mode for the projected demo
- MVP: /admin read-only camera list + alert-rule thresholds editable on stage + model registry row
- STRETCH: intercepting parallel route alerts/@drawer/(.)[id] instead of the ?open= search param
- STRETCH: clustered incident bubbles above z13 from a second clustered source
- STRETCH: HLS preview via MediaMTX + hls.js on the focused camera tile only
- STRETCH: co-travellers tab on /vehicle/[plate] (vehicles repeatedly co-observed)
- STRETCH: pre-baked muted Mapbox Studio style replacing the runtime restyle loop
- STRETCH: @next/bundle-analyzer in CI and a hard first-load budget check
- STRETCH: /admin audit log with full-text filter, and DPDP retention purge UI with typed confirmation
- STRETCH: undo stack beyond a single item in /review, and multi-reviewer conflict display
- STRETCH: printable alert brief (PDF via browser print stylesheet) from /alerts/[id]

## Appendix — Risks

- Realtime silently delivers nothing if the DB section forgets `ALTER PUBLICATION supabase_realtime ADD TABLE ...`. Mitigation: a dev-only banner in the app shell that shows the channel state ('SUBSCRIBED'/'CHANNEL_ERROR') and warns if zero events arrive within 30 s of a known-active worker.
- Realtime payloads carry no joined columns, so alert rows arriving live would render without a camera name and visibly 'pop' when enriched. Mitigation: load all cameras into a Map in a context provider at shell mount and enrich client-side before the row is committed.
- mapbox-gl is ~230 kB gzip and will blow the bundle budget if statically imported anywhere. Mitigation: only ever import it inside a component loaded via dynamic(..., {ssr:false}); add a lint rule / grep in the pre-submission checklist for `from "mapbox-gl"` outside components/map/.
- Runtime basemap muting depends on Mapbox layer-id naming; a rename leaves colourful roads and breaks the design law on stage. Mitigation: fall back to NEXT_PUBLIC_MAPBOX_STYLE_URL pointing at a pre-muted Studio style, prepared the night before.
- A missing or rate-limited Mapbox token blanks the hero screen mid-demo. Mitigation: the map error state renders the same trajectory as an ordered table, and a Leaflet + OSM raster fallback component is kept behind a NEXT_PUBLIC_MAP_ENGINE flag.
- 40 plate_reads/s of Realtime inserts can cause a re-render storm that freezes the UI on a 16 GB laptop already running ONNX workers. Mitigation: 500 ms buffered flush, hard row caps, id-keyed memoised rows, and virtualisation above 200 rows.
- Polling latest.jpg for 9 tiles adds ~0.5 MB/s and extra Storage requests that compete with the workers writing them. Mitigation: IntersectionObserver + document.hidden gating, 3 s background interval, and a demo-day option to drop the wall to 4 tiles.
- Next 16 makes params/searchParams Promises; copying a Next 14 snippet produces a confusing runtime error under time pressure. Mitigation: every page signature typed as Promise<...> and awaited, documented once at the top of the file tree.
- TanStack Query without initialDataUpdatedAt refetches on mount and visibly re-sorts the table ~300 ms after paint, which reads as a bug to judges. Mitigation: the pattern is mandatory on every list page and is in the code review checklist.
- Corrections in /review are worthless as training data if crops are not retained alongside them. Mitigation: the review submission contract requires crop_path to already exist in the crops bucket; the export endpoint fails loudly listing missing crops rather than emitting a silently short dataset.

## Appendix — Open Questions

- Does the backend return trajectory legs pre-computed (distance_m, seconds, speed_kmph, implausible) or must the client derive them? This section assumes the backend computes them so the clone threshold lives in one place (/admin alert rules).
- Are evidence clips written as plain 6-second MP4 (assumed) or HLS segments? The drawer's <video> element depends on MP4.
- Is the crops bucket public-read or does the frontend need signed URLs? Public read is assumed for demo speed; signed URLs would require an extra endpoint and a URL cache.
- What is the exact shape of alerts.payload jsonb per alert_type? The drawer renders it as a generic definition list, but per-type formatting (units on speed, metres on distance) needs the key names fixed.
- Does the OCR worker emit per-character confidences and top-5 beam candidates today, or only a single string plus a score? The review screen's char-confidence strip and candidate list degrade to a plain input without them.
- Which city/bbox is the demo set in, so map initial center/zoom and the camera coordinate fixtures can be committed rather than configured?
- Is there a fixed reviewer identity for the demo, or does /review require real per-user auth to attribute plate_review rows?
