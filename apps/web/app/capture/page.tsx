"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Phone-as-camera.
 *
 *  Deliberately outside the (console) layout: this runs on a phone in a
 *  pocket, not in a control room. No sidebar, no keyboard shortcuts, big
 *  touch targets, and it keeps working with the screen dimmed.
 *
 *  The camera only opens in a secure context. On a plain LAN address the
 *  browser refuses without explanation, so this page says so plainly and
 *  tells the operator how to fix it.
 */
export default function CapturePage() {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const inFlight = useRef(false);

  const [camId, setCamId] = useState("");
  const [name, setName] = useState("");
  const [ready, setReady] = useState(false);
  const [sending, setSending] = useState(false);
  const [facing, setFacing] = useState<"environment" | "user">("environment");
  const [fps, setFps] = useState(2);
  const [modes, setModes] = useState<Record<string, boolean>>(
    { face: false, plate: false, threat: false });
  const [sent, setSent] = useState(0);
  const [failed, setFailed] = useState(0);
  const [faces, setFaces] = useState<any[]>([]);
  const [plates, setPlates] = useState<any[]>([]);
  const [threat, setThreat] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [secure, setSecure] = useState(true);
  const [coords, setCoords] = useState<{ lat: number; lon: number } | null>(null);

  useEffect(() => {
    // A stable id per device so reconnecting rejoins the same camera
    // rather than creating a new one on the wall every time.
    let id = localStorage.getItem("netra-device-id");
    if (!id) {
      id = `phone-${Math.random().toString(36).slice(2, 8)}`;
      localStorage.setItem("netra-device-id", id);
    }
    setCamId(id);
    setName(localStorage.getItem("netra-device-name") ?? "");
    setSecure(window.isSecureContext);

    // Registration only succeeds in a secure context, which is the same
    // condition the camera needs — so a failure here is not worth
    // surfacing separately.
    navigator.serviceWorker?.register("/sw.js").catch(() => {});
  }, []);

  const start = useCallback(async () => {
    try {
      streamRef.current?.getTracks().forEach((t) => t.stop());
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: facing },
                 width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setReady(true);
      setError(null);
    } catch (e: any) {
      setError(
        !window.isSecureContext
          ? "The browser blocks the camera on an insecure address. Open this page over HTTPS."
          : e?.name === "NotAllowedError"
          ? "Camera permission denied. Allow it and reload."
          : `${e?.name ?? "Error"}: ${e?.message ?? String(e)}`);
      setReady(false);
    }
  }, [facing]);

  const stop = useCallback(() => {
    setSending(false);
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setReady(false);
  }, []);

  useEffect(() => stop, [stop]);

  // Optional location, so a mobile camera lands in the right place on the
  // map instead of at 0,0 in the Atlantic.
  const locate = () => {
    navigator.geolocation?.getCurrentPosition(
      (p) => setCoords({ lat: p.coords.latitude, lon: p.coords.longitude }),
      () => setError("Location permission denied — the camera will have no position."),
      { enableHighAccuracy: true, timeout: 8000 });
  };

  const sendFrame = useCallback(async () => {
    // One request at a time. A phone on mobile data will otherwise queue
    // frames faster than it can upload them and fall further behind.
    if (inFlight.current) return;
    const v = videoRef.current, c = canvasRef.current;
    if (!v || !c || !v.videoWidth) return;

    const maxW = 960;
    const scale = Math.min(1, maxW / v.videoWidth);
    c.width = Math.round(v.videoWidth * scale);
    c.height = Math.round(v.videoHeight * scale);
    c.getContext("2d")?.drawImage(v, 0, 0, c.width, c.height);
    const img = c.toDataURL("image/jpeg", 0.7);

    inFlight.current = true;
    try {
      const res = await fetch("/api/v1/devices/frame", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          camera_id: camId, image: img,
          name: name || undefined,
          lat: coords?.lat, lon: coords?.lon,
          analyse: Object.keys(modes).filter((k) => modes[k]).join(",") || "none",
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      setFaces(d.faces ?? []);
      setPlates(d.plates ?? []);
      setThreat(d.threat ?? null);
      setSent((n) => n + 1);
      setError(d.analysis_error ?? null);
    } catch (e: any) {
      setFailed((n) => n + 1);
      setError(String(e?.message ?? e));
    } finally { inFlight.current = false; }
  }, [camId, name, coords, modes]);

  useEffect(() => {
    if (!sending || !ready) return;
    const id = setInterval(sendFrame, Math.max(300, 1000 / fps));
    return () => clearInterval(id);
  }, [sending, ready, fps, sendFrame]);

  const hit = faces.find((f) => f.match);

  // Boxes come back in the coordinates of the frame that was *uploaded*
  // (capped at 960 px wide), not the frame being displayed, so everything
  // is scaled by one factor rather than each overlay guessing.
  const shownW = videoRef.current?.clientWidth ?? 0;
  const capturedW = Math.min(960, videoRef.current?.videoWidth || 960);
  const scale = shownW ? shownW / capturedW : 1;

  const overlays: { box: number[]; colour: string; label?: string; mono?: boolean }[] = [
    ...faces.map((f) => ({
      box: f.box,
      colour: f.match ? (f.match.decision === "confirm" ? "#FF4D4D" : "#FFA640") : "#38D07A",
      label: f.match ? `${f.match.name} ${(f.match.score * 100).toFixed(0)}%` : undefined,
    })),
    ...plates.map((p) => ({
      // Amber, never green: a one-frame plate is a candidate for review,
      // and the colour should not promise more than the evidence does.
      box: p.box,
      colour: p.grammar_valid ? "#38D07A" : "#FFA640",
      label: `${p.plate} ${(p.confidence * 100).toFixed(0)}%`,
      mono: true,
    })),
    ...((threat?.objects ?? []) as any[]).map((o) => ({
      box: o.box,
      colour: "#FFA640",
      label: `${o.class} ${(o.confidence * 100).toFixed(0)}%`,
    })),
  ];

  return (
    <div className="flex min-h-screen flex-col" style={{ background: "#0B1220" }}>
      <header className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-2">
          <svg viewBox="0 0 64 64" width="22" height="22" style={{ color: "#6FA3DE" }}>
            <circle cx="32" cy="32" r="25.6" fill="none" stroke="currentColor" strokeWidth="2.6" />
            <circle cx="32" cy="32" r="9.6" fill="none" stroke="currentColor" strokeWidth="2.6" />
            <circle cx="32" cy="32" r="4" fill="currentColor" />
          </svg>
          <span className="text-[15px] font-semibold text-white">NETRA Camera</span>
        </div>
        <span className="mono text-[11px]" style={{ color: "#8FA6C4" }}>{camId}</span>
      </header>

      {!secure && (
        <div className="mx-4 mb-3 rounded p-3" style={{ background: "#3A1212", color: "#FFD9D9" }}>
          <div className="text-[13px] font-semibold">Camera unavailable on this address</div>
          <p className="mt-1 text-[12px]">
            Browsers only allow camera access over HTTPS (or on localhost). Open this
            page through a tunnel — run <span className="mono">python scripts/connect.py</span> on
            the server and use the HTTPS link it prints.
          </p>
        </div>
      )}

      <div className="relative flex-1 bg-black">
        <video ref={videoRef} playsInline muted
               className="h-full w-full object-contain" style={{ maxHeight: "60vh" }} />
        <canvas ref={canvasRef} className="hidden" />

        {overlays.map((o, i) => (
          <div key={i} className="pointer-events-none absolute"
               style={{ left: o.box[0] * scale, top: o.box[1] * scale,
                        width: (o.box[2] - o.box[0]) * scale,
                        height: (o.box[3] - o.box[1]) * scale,
                        border: `2px solid ${o.colour}` }}>
            {o.label && (
              <span className={"absolute -top-6 left-0 whitespace-nowrap px-1.5 py-0.5 " +
                               "text-[11px] font-semibold text-white " +
                               (o.mono ? "mono" : "")}
                    style={{ background: o.colour }}>
                {o.label}
              </span>
            )}
          </div>
        ))}

        {!ready && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-[13px]" style={{ color: "#5E6878" }}>camera off</span>
          </div>
        )}
      </div>

      {hit && (
        <div className="px-4 py-3 text-center" style={{ background: "#7A1010" }}>
          <div className="text-[11px] font-semibold tracking-wider text-white opacity-80">
            WATCHLIST CANDIDATE
          </div>
          <div className="text-[20px] font-bold text-white">{hit.match.name}</div>
          <div className="mono text-[12px] text-white opacity-80">
            {(hit.match.score * 100).toFixed(1)}% · pending operator confirmation
          </div>
        </div>
      )}

      {plates.length > 0 && (
        <div className="px-4 py-2" style={{ background: "#12233A" }}>
          {plates.map((p, i) => (
            <div key={`${p.plate}-${i}`} className="flex items-baseline justify-between py-0.5">
              <span className="mono text-[17px] font-bold text-white">{p.plate}</span>
              <span className="text-[11px]" style={{ color: "#8FA6C4" }}>
                {(p.confidence * 100).toFixed(0)}% · 1 frame · queued for review
              </span>
            </div>
          ))}
        </div>
      )}

      {threat && threat.decision !== "none" && (
        <div className="px-4 py-2" style={{ background: "#4A3208" }}>
          <div className="text-[11px] font-semibold tracking-wider"
               style={{ color: "#FFD79A" }}>
            THREAT EVIDENCE · score {(threat.score * 100).toFixed(0)}%
          </div>
          {threat.contributions.map((c: any, i: number) => (
            <div key={i} className="text-[12px]" style={{ color: "#FFE8C4" }}>
              {c.kind}: {c.detail}
            </div>
          ))}
        </div>
      )}

      <div className="space-y-3 p-4">
        <input
          className="w-full rounded px-3 py-2.5 text-[15px]"
          style={{ background: "#141E31", color: "#fff", border: "1px solid #24344F" }}
          placeholder="Camera name (e.g. Gate 3 handheld)"
          value={name}
          onChange={(e) => { setName(e.target.value);
                             localStorage.setItem("netra-device-name", e.target.value); }}
        />

        <div className="grid grid-cols-2 gap-3">
          {!ready ? (
            <button onClick={start} className="col-span-2 rounded py-3.5 text-[15px] font-semibold text-white"
                    style={{ background: "#17509C" }}>
              Start camera
            </button>
          ) : (
            <>
              <button onClick={() => setSending((s) => !s)}
                      className="rounded py-3.5 text-[15px] font-semibold text-white"
                      style={{ background: sending ? "#A81616" : "#14713A" }}>
                {sending ? "Stop streaming" : "Start streaming"}
              </button>
              <button onClick={() => { setFacing((f) => f === "environment" ? "user" : "environment");
                                       setTimeout(start, 100); }}
                      className="rounded py-3.5 text-[15px] text-white"
                      style={{ background: "#141E31", border: "1px solid #24344F" }}>
                Flip camera
              </button>
            </>
          )}
        </div>

        <div className="grid grid-cols-3 gap-3">
          {([["face", "Faces"], ["plate", "Plates"], ["threat", "Threat"]] as const)
            .map(([key, label]) => (
            <button key={key}
                    onClick={() => setModes((m) => ({ ...m, [key]: !m[key] }))}
                    className="rounded py-2.5 text-[13px] font-semibold"
                    style={{ background: modes[key] ? "#17509C" : "#141E31",
                             color: "#fff", border: "1px solid #24344F" }}>
              {label}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <button onClick={locate} className="rounded py-2.5 text-[13px] text-white"
                  style={{ background: "#141E31", border: "1px solid #24344F" }}>
            {coords ? "Located" : "Set location"}
          </button>
          <select value={fps} onChange={(e) => setFps(Number(e.target.value))}
                  className="rounded px-2 py-2.5 text-[13px] text-white"
                  style={{ background: "#141E31", border: "1px solid #24344F" }}>
            <option value={1}>1 fps</option>
            <option value={2}>2 fps</option>
            <option value={4}>4 fps</option>
          </select>
        </div>

        <div className="flex justify-between text-[12px]" style={{ color: "#8FA6C4" }}>
          <span>sent <span className="mono">{sent}</span></span>
          <span>failed <span className="mono">{failed}</span></span>
          <span>{coords ? `${coords.lat.toFixed(4)}, ${coords.lon.toFixed(4)}` : "no location"}</span>
        </div>

        {error && (
          <p className="text-[12px]" style={{ color: "#FF8A8A" }}>{error}</p>
        )}
        <p className="text-[11px]" style={{ color: "#5E6878" }}>
          Frames appear on the live wall as camera <span className="mono">{camId}</span>.
          A handheld camera sees each vehicle once, so plate reads here go to the
          review queue rather than being published as confirmed sightings, and
          face and threat results are candidates for an operator to judge.
        </p>
      </div>
    </div>
  );
}
