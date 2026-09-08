"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/Shell";
import { CameraView, useCamera } from "@/components/Camera";
import { EmptyState, ErrorState, PanelHeader } from "@/components/ui";
import { dateTime } from "@/lib/format";

interface Person {
  id: string; name: string; category: string; case_ref: string | null;
  added_by: string; added_at: string; expires_at: string | null;
  templates: number; matches: number; is_active: number;
}

const CATEGORIES = [
  ["wanted", "Wanted"], ["missing", "Missing person"],
  ["person_of_interest", "Person of interest"],
];

export default function PersonsPage() {
  const cam = useCamera();
  const [people, setPeople] = useState<Person[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const [name, setName] = useState("");
  const [category, setCategory] = useState("wanted");
  const [caseRef, setCaseRef] = useState("");
  const [expiry, setExpiry] = useState("");
  const [shot, setShot] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(() => {
    fetch("/api/v1/persons").then((r) => r.json())
      .then((d) => { setPeople(d.results ?? []); setError(null); })
      .catch(setError);
  }, []);
  useEffect(load, [load]);

  const takeShot = () => {
    const img = cam.capture();
    if (img) { setShot(img); setMsg(null); }
  };

  const pickFile = (f: File) => {
    const r = new FileReader();
    r.onload = () => { setShot(String(r.result)); setMsg(null); };
    r.readAsDataURL(f);
  };

  const enrol = async () => {
    if (!shot || !name.trim()) return;
    setBusy(true); setMsg(null);
    try {
      const res = await fetch("/api/v1/persons", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          name: name.trim(), image: shot, category,
          case_ref: caseRef.trim() || null,
          expires_at: expiry ? `${expiry}T00:00:00Z` : null,
          added_by: "operator",
        }),
      });
      const d = await res.json();
      if (!res.ok) {
        setMsg({ ok: false, text: d.detail ?? `HTTP ${res.status}` });
      } else {
        setMsg({ ok: true,
                 text: `Enrolled ${d.name} — ${d.templates} template(s), quality ${d.quality}` });
        setName(""); setCaseRef(""); setShot(null); load();
      }
    } catch (e: any) {
      setMsg({ ok: false, text: String(e?.message ?? e) });
    } finally { setBusy(false); }
  };

  const remove = async (id: string) => {
    if (!confirm(`Remove ${id} from the watchlist and delete its face templates?`)) return;
    await fetch(`/api/v1/persons/${id}?reason=removed+by+operator`, { method: "DELETE" });
    load();
  };

  return (
    <>
      <PageHeader
        title="Person watchlist"
        description="Enrolment is authorised, audited and time-bounded. Templates are deleted on removal."
      >
        <button className="btn" onClick={load}>Refresh</button>
      </PageHeader>

      <div className="grid gap-6 p-6 xl:grid-cols-[1fr_1.1fr]">
        {/* ---------------- enrol ---------------- */}
        <section className="panel overflow-hidden">
          <PanelHeader title="Enrol a person">
            <span className="label-micro">one face per photo</span>
          </PanelHeader>

          <CameraView videoRef={cam.videoRef} canvasRef={cam.canvasRef}
                      ready={cam.ready} height={300} />

          <div className="flex flex-wrap gap-2 border-b border-hairline p-3">
            {!cam.ready ? (
              <button className="btn" onClick={cam.start}>Start camera</button>
            ) : (
              <>
                <button className="btn btn-primary" onClick={takeShot}>Capture</button>
                <button className="btn" onClick={cam.stop}>Stop camera</button>
              </>
            )}
            <button className="btn" onClick={() => fileRef.current?.click()}>
              Upload photo
            </button>
            <input ref={fileRef} type="file" accept="image/*" className="hidden"
                   onChange={(e) => e.target.files?.[0] && pickFile(e.target.files[0])} />
          </div>

          {cam.error && (
            <div className="px-3 pt-3"><ErrorState error={{ message: cam.error }} compact /></div>
          )}

          <div className="grid gap-3 p-4 sm:grid-cols-2">
            <div className="sm:col-span-2 flex items-center gap-3">
              <div className="h-20 w-20 shrink-0 overflow-hidden border border-hairline-strong
                              bg-surface-sunken">
                {shot ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={shot} alt="Captured face" className="h-full w-full object-cover" />
                ) : (
                  <div className="flex h-full items-center justify-center">
                    <span className="label-micro">no photo</span>
                  </div>
                )}
              </div>
              <p className="text-[11px] text-ink-subtle">
                Capture from the camera or upload a photograph. The image is converted
                to a 512-dimension template; the photo itself is not stored.
              </p>
            </div>

            <div>
              <label className="label-micro" htmlFor="pname">Name</label>
              <input id="pname" className="input mt-1.5" value={name}
                     onChange={(e) => setName(e.target.value)} placeholder="Full name" />
            </div>
            <div>
              <label className="label-micro" htmlFor="pcat">Category</label>
              <select id="pcat" className="input mt-1.5" value={category}
                      onChange={(e) => setCategory(e.target.value)}>
                {CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <div>
              <label className="label-micro" htmlFor="pcase">Case reference</label>
              <input id="pcase" className="input mt-1.5" value={caseRef}
                     onChange={(e) => setCaseRef(e.target.value)}
                     placeholder="FIR 118/2026" />
            </div>
            <div>
              <label className="label-micro" htmlFor="pexp">Enrolment expires</label>
              <input id="pexp" type="date" className="input mt-1.5" value={expiry}
                     onChange={(e) => setExpiry(e.target.value)} />
            </div>

            <div className="sm:col-span-2 flex items-center gap-3">
              <button className="btn btn-primary" disabled={busy || !shot || !name.trim()}
                      onClick={enrol}>
                {busy ? "Enrolling…" : "Enrol person"}
              </button>
              {msg && (
                <span className="text-[12px]"
                      style={{ color: msg.ok ? "var(--ok)" : "var(--critical)" }}>
                  {msg.text}
                </span>
              )}
            </div>
          </div>
        </section>

        {/* ---------------- watchlist ---------------- */}
        <section className="panel overflow-hidden">
          <PanelHeader title="Enrolled persons">
            <span className="label-micro">{people.length} active</span>
          </PanelHeader>
          {error ? (
            <ErrorState error={error} onRetry={load} />
          ) : people.length === 0 ? (
            <EmptyState title="Nobody enrolled"
                        hint="The scanner matches against this list only. With it empty, no face is ever identified." />
          ) : (
            <table className="w-full border-collapse">
              <thead className="table-head">
                <tr>
                  <th>Name</th><th>Category</th><th>Case</th>
                  <th className="num">Templates</th><th className="num">Enrolled</th><th></th>
                </tr>
              </thead>
              <tbody>
                {people.map((p) => (
                  <tr key={p.id} className="table-row">
                    <td className="text-[13px] text-ink">{p.name}</td>
                    <td>
                      <span className="pill" style={{
                        color: p.category === "wanted" ? "var(--critical)" : "var(--medium)",
                        background: p.category === "wanted"
                          ? "var(--critical-wash)" : "var(--medium-wash)" }}>
                        {p.category.replace(/_/g, " ")}
                      </span>
                    </td>
                    <td className="mono text-[12px] text-ink-muted">{p.case_ref ?? "—"}</td>
                    <td className="num text-[12px]">{p.templates}</td>
                    <td className="num text-[12px] text-ink-muted whitespace-nowrap">
                      {dateTime(p.added_at)}
                    </td>
                    <td className="text-right">
                      <button className="btn btn-ghost h-7 text-[12px]"
                              onClick={() => remove(p.id)}>Remove</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </>
  );
}
