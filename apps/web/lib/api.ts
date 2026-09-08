/** Typed client for the NETRA API.
 *
 *  All requests go to same-origin `/api/*`, which next.config.mjs rewrites
 *  to the FastAPI service. That keeps the API location out of the client
 *  bundle and avoids CORS entirely.
 *
 *  Deliberate: there are NO hardcoded fallback numbers here. If the API is
 *  down the UI must say so, not quietly render invented statistics — a
 *  command centre showing fake counts is worse than one showing an error.
 */

export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type AlertStatus =
  | "new" | "acknowledged" | "investigating" | "resolved" | "false_positive";
export type AlertType =
  | "cloned_plate" | "speeding" | "loitering" | "watchlist_hit"
  | "hit_and_run" | "anomaly" | "no_plate" | "convoy";

export interface Camera {
  id: string; name: string; lat: number; lon: number;
  bearing_deg: number | null; zone_id: string | null; zone_name: string | null;
  zone_kind: string | null; road_name: string | null; is_active: number;
  last_sighting_at: string | null; sighting_count: number;
  /** Derived server-side from the age of the worker's frame,
   *  not from the database — a camera row can exist with no
   *  worker attached. */
  is_streaming: boolean; frame_age_s: number | null;
}

export interface Sighting {
  id: number; plate: string; camera_id: string; camera_name: string;
  seen_at: string; confidence: number; frame_count: number;
  color: string | null; vehicle_type: string | null; speed_kmh: number | null;
  lat: number; lon: number; source: string; on_watchlist?: number;
}

export interface Alert {
  id: number; alert_type: AlertType; severity: Severity; status: AlertStatus;
  plate: string | null; camera_id: string | null; camera_name: string | null;
  occurred_at: string; title: string; detail: string; confidence: number;
  evidence: Record<string, any>;
  lat: number | null; lon: number | null;
  acknowledged_by: string | null; acknowledged_at: string | null;
  recent_sightings?: TrajectoryPoint[];
}

export interface TrajectoryPoint {
  id: number; seen_at: string; confidence: number; speed_kmh: number | null;
  camera_id: string; camera_name: string; lat: number; lon: number;
  zone_id: string | null;
}

export interface Leg {
  from_camera: string; to_camera: string; distance_m: number;
  elapsed_s: number; implied_kmh: number | null;
}

export interface Trajectory {
  plate: string; point_count: number;
  points: TrajectoryPoint[]; legs: Leg[];
  bounds: { min_lat: number; max_lat: number; min_lon: number; max_lon: number } | null;
}

export interface ReviewItem {
  id: number; track_id: string; camera_id: string; camera_name: string;
  best_guess: string; confidence: number; frame_count: number;
  candidates: { plate: string; score: number }[];
  seen_at: string; status: string;
  /** Relative to /evidence/ — the crop the model actually read. */
  crop_path: string | null;
}

export interface Stats {
  generated_at: string;
  sightings_total: number; sightings_24h: number; sightings_1h: number;
  vehicles_total: number; cameras_total: number; cameras_active: number;
  alerts_open: number; alerts_total: number;
  watchlist_active: number; review_pending: number;
  accuracy: {
    auto_accept_rate: number; operator_correction_rate: number;
    reviewed_count: number; auto_accept_threshold: number;
  };
  live_subscribers: number;
}

/** A failure that the UI can reason about instead of a bare Error.
 *
 *  The distinction that matters to an operator is not the status code, it
 *  is "is the backend down" versus "this particular request was refused".
 *  The first means the whole console is blind; the second means one panel
 *  is empty. They deserve different screens.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly path: string;
  readonly detail: string;
  /** True when the request never reached the API at all. */
  readonly offline: boolean;

  constructor(path: string, status: number, detail: string, offline = false) {
    super(offline
      ? `Cannot reach the API (${path})`
      : `${status} on ${path}${detail ? ` — ${detail}` : ""}`);
    this.name = "ApiError";
    this.path = path;
    this.status = status;
    this.detail = detail;
    this.offline = offline;
  }

  /** Plain language for an operator, not a stack trace. */
  get friendly(): string {
    if (this.offline) return "The NETRA API is not responding.";
    if (this.status === 404) return "That record no longer exists.";
    if (this.status === 401 || this.status === 403)
      return "You are not authorised to view this.";
    if (this.status >= 500) return "The API failed while handling this request.";
    return this.detail || "The request was rejected.";
  }

  /** What the operator should actually do about it. */
  get hint(): string {
    if (this.offline || this.status >= 500)
      return "Start it with:  python scripts/start.py";
    return "";
  }
}

async function get<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { cache: "no-store", ...init });
  } catch (cause) {
    // fetch only rejects when the request never completed — DNS, refused
    // connection, aborted. A dead API lands here, and it is the single
    // most common failure during development and on demo day.
    throw new ApiError(path, 0, String(cause), true);
  }

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    let detail = body.slice(0, 300);
    try {
      // FastAPI returns {"detail": "..."} — surface that, not raw JSON.
      const parsed = JSON.parse(body);
      if (typeof parsed?.detail === "string") detail = parsed.detail;
    } catch {
      /* not JSON; the raw text is the best we have */
    }
    throw new ApiError(path, res.status, detail);
  }

  try {
    return (await res.json()) as T;
  } catch (cause) {
    // A 200 that is not JSON usually means a proxy or error page was
    // returned instead of the API's response.
    throw new ApiError(path, res.status,
      `Expected JSON but got something else (${String(cause)})`);
  }
}

export const api = {
  stats: () => get<Stats>("/api/stats"),
  timeline: (hours = 24) =>
    get<{ buckets: { hour: string; n: number }[] }>(`/api/stats/timeline?hours=${hours}`),

  cameras: () => get<{ results: Camera[]; streaming: number; count: number }>(
    "/api/cameras"),
  zones: () => get<{ results: any[] }>("/api/zones"),

  sightings: (limit = 60) =>
    get<{ results: Sighting[] }>(`/api/sightings?limit=${limit}`),

  alerts: (params: Record<string, string | number | undefined> = {}) => {
    const q = new URLSearchParams(
      Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== "")
        .map(([k, v]) => [k, String(v)])
    );
    return get<{ results: Alert[]; next_cursor: number | null }>(`/api/alerts?${q}`);
  },
  alert: (id: number) => get<Alert>(`/api/alerts/${id}`),
  alertSummary: () =>
    get<{ open_by_severity: Record<string, number>; by_type: Record<string, number>; open_total: number }>(
      "/api/alerts/summary"),
  updateAlert: (id: number, status: AlertStatus, note?: string) =>
    get<Alert>(`/api/alerts/${id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ status, actor: "operator", note }),
    }),

  search: (q: string, reason?: string) =>
    get<{ results: any[] }>(
      `/api/vehicles/search?q=${encodeURIComponent(q)}` +
      (reason ? `&reason=${encodeURIComponent(reason)}` : "")),
  vehicle: (plate: string) => get<any>(`/api/vehicles/${encodeURIComponent(plate)}`),
  trajectory: (plate: string) =>
    get<Trajectory>(`/api/vehicles/${encodeURIComponent(plate)}/trajectory`),

  review: () => get<{ results: ReviewItem[] }>("/api/review?status=pending"),
  decideReview: (id: number, decision: "confirmed" | "corrected" | "rejected",
                 corrected_plate?: string) =>
    get<any>(`/api/review/${id}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ decision, corrected_plate, reviewed_by: "operator" }),
    }),

  watchlist: () => get<{ results: any[] }>("/api/watchlist"),
  audit: (limit = 100) => get<{ results: any[] }>(`/api/audit?limit=${limit}`),
  aiStatus: () => get<any>("/api/ai/status"),
};
