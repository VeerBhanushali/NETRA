"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/Shell";
import { EmptyState, ErrorState, PanelHeader } from "@/components/ui";
import { api, type ReviewItem } from "@/lib/api";
import { dateTime } from "@/lib/format";

/** Keyboard-first triage: the operator should never need the mouse.
 *  Enter confirms, typing a correction and Enter corrects, Escape rejects.
 *  A queue that takes three clicks per item does not get worked. */
export default function ReviewPage() {
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [i, setI] = useState(0);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    api.review()
      .then((r) => { setItems(r.results); setI(0); setError(null); })
      .catch(setError);
  }, []);
  useEffect(load, [load]);

  const current = items[i];
  useEffect(() => {
    setDraft(current?.best_guess ?? "");
    inputRef.current?.focus();
  }, [current]);

  const decide = async (decision: "confirmed" | "corrected" | "rejected") => {
    if (!current || busy) return;
    setBusy(true);
    try {
      const corrected = decision === "corrected" ? draft.trim().toUpperCase() : undefined;
      await api.decideReview(current.id, decision, corrected);
      setItems((prev) => prev.filter((x) => x.id !== current.id));
      setI((n) => Math.min(n, Math.max(0, items.length - 2)));
      setDone((d) => d + 1);
    } finally { setBusy(false); }
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const changed = draft.trim().toUpperCase() !== current?.best_guess;
      decide(changed ? "corrected" : "confirmed");
    } else if (e.key === "Escape") {
      e.preventDefault();
      decide("rejected");
    }
  };

  return (
    <>
      <PageHeader
        title="Review queue"
        description="Reads below the auto-accept threshold. Nothing here has been published as fact."
      >
        <span className="label-micro">{items.length} pending · {done} cleared</span>
        <button className="btn" onClick={load}>Refresh</button>
      </PageHeader>

      <div className="p-6">
        {error ? (
          <div className="panel"><ErrorState error={error} onRetry={load} /></div>
        ) : items.length === 0 ? (
          <div className="panel">
            <EmptyState
              title="Queue is empty"
              hint="Every read cleared the auto-accept threshold, so no human judgement was required."
            />
          </div>
        ) : (
          <div className="grid gap-6 xl:grid-cols-[1.1fr_1fr]">
            <section className="panel">
              <PanelHeader title={`Item ${i + 1} of ${items.length}`}>
                <span className="label-micro">{current?.camera_name}</span>
              </PanelHeader>

              <div className="p-5">
                {/* The actual crop the model read. Reviewing a plate you
                    cannot see is rubber-stamping, not review. */}
                <div className="flex h-32 items-center justify-center overflow-hidden
                                border border-hairline-strong bg-[#0b0b0d]">
                  {current?.crop_path ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={`/evidence/${current.crop_path}`}
                         alt={`Plate crop read as ${current.best_guess}`}
                         className="max-h-full max-w-full object-contain"
                         style={{ imageRendering: "pixelated" }} />
                  ) : (
                    <span className="label-micro" style={{ color: "var(--ink-subtle)" }}>
                      no crop stored for this read
                    </span>
                  )}
                </div>

                <div className="mt-5">
                  <label className="label-micro" htmlFor="plate-input">
                    Plate — correct it or press Enter to confirm
                  </label>
                  <input
                    id="plate-input" ref={inputRef} className="input plate mt-1.5 text-[20px] h-12"
                    value={draft} disabled={busy}
                    onChange={(e) => setDraft(e.target.value.toUpperCase())}
                    onKeyDown={onKey}
                  />
                </div>

                <div className="mt-3 flex items-center gap-4">
                  <div>
                    <div className="label-micro">Model confidence</div>
                    <div className="mono text-[15px]" style={{ color: "var(--high)" }}>
                      {(current!.confidence * 100).toFixed(1)}%
                    </div>
                  </div>
                  <div>
                    <div className="label-micro">Frames voted</div>
                    <div className="mono text-[15px] text-ink">{current!.frame_count}</div>
                  </div>
                  <div>
                    <div className="label-micro">Seen</div>
                    <div className="mono text-[13px] text-ink-muted">
                      {dateTime(current!.seen_at)}
                    </div>
                  </div>
                </div>

                <div className="mt-5 flex gap-2">
                  <button className="btn btn-primary" disabled={busy}
                          onClick={() => decide(
                            draft.trim().toUpperCase() !== current!.best_guess
                              ? "corrected" : "confirmed")}>
                    {draft.trim().toUpperCase() !== current!.best_guess
                      ? "Save correction" : "Confirm"}
                    <kbd className="kbd">Enter</kbd>
                  </button>
                  <button className="btn" disabled={busy} onClick={() => decide("rejected")}>
                    Unreadable <kbd className="kbd">Esc</kbd>
                  </button>
                  <button className="btn btn-ghost" disabled={busy}
                          onClick={() => setI((n) => (n + 1) % items.length)}>
                    Skip
                  </button>
                </div>
              </div>
            </section>

            <section className="panel">
              <PanelHeader title="What the model considered" />
              <div className="p-5">
                <p className="text-[12px] leading-relaxed text-ink-muted">
                  These are the candidate strings produced by temporal voting across
                  the tracked vehicle&rsquo;s frames, with their vote scores. The gap
                  between the top two is why this read did not auto-accept.
                </p>
                <table className="mt-4 w-full border-collapse">
                  <thead className="table-head">
                    <tr><th>Candidate</th><th className="num">Vote score</th></tr>
                  </thead>
                  <tbody>
                    {/* Keyed by index, not by plate: two candidates can be
                        the same string arriving from different frames, and
                        a duplicate key makes React drop one of the rows. */}
                    {(current?.candidates ?? []).map((c, n) => (
                      <tr key={`${c.plate}-${n}`} className="table-row">
                        <td>
                          <button
                            className="plate text-[14px] hover:underline underline-offset-2"
                            onClick={() => setDraft(c.plate)}
                          >
                            {c.plate}
                          </button>
                          {n === 0 && (
                            <span className="ml-2 label-micro">top</span>
                          )}
                        </td>
                        <td className="num text-[12px]">{(c.score * 100).toFixed(1)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="mt-4 text-[11px] text-ink-subtle">
                  Corrections are recorded and become the accuracy signal: a rising
                  correction rate means the model has drifted or conditions changed.
                </p>
              </div>
            </section>
          </div>
        )}
      </div>
    </>
  );
}
