"use client";

/** Last-resort boundary: catches failures in the root layout itself, so
 *  even a broken shell renders something an operator can act on. */
export default function GlobalError({
  error, reset,
}: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", padding: "3rem",
                     background: "#F2F4F7", color: "#0B0F17" }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>
          NETRA could not start this page
        </h1>
        <p style={{ fontSize: 14, color: "#4A5464", marginTop: 8 }}>
          {error.message}
        </p>
        <button
          onClick={reset}
          style={{ marginTop: 20, height: 32, padding: "0 14px", fontSize: 13,
                   border: "1px solid #CBD2DD", borderRadius: 3,
                   background: "#fff", cursor: "pointer" }}
        >
          Try again
        </button>
      </body>
    </html>
  );
}
