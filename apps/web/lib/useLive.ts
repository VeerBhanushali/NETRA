"use client";

import { useEffect, useRef, useState } from "react";

export interface LiveEvent {
  event: "sighting.created" | "alert.created" | "alert.updated"
       | "review.queued" | "review.decided";
  data: any;
}

/** Subscribe to the server's SSE feed.
 *
 *  EventSource reconnects on its own, so the dashboard recovers from an
 *  API restart without a page refresh — which matters when the API is
 *  being restarted repeatedly during a hackathon.
 */
export function useLive(onEvent: (e: LiveEvent) => void) {
  const [connected, setConnected] = useState(false);
  // Keep the latest callback in a ref so re-renders don't tear down and
  // rebuild the connection on every parent render.
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    const src = new EventSource("/api/stream");
    src.onopen = () => setConnected(true);
    src.onerror = () => setConnected(false);
    src.onmessage = (msg) => {
      try {
        handler.current(JSON.parse(msg.data) as LiveEvent);
      } catch {
        /* keepalive comments and malformed frames are not fatal */
      }
    };
    return () => src.close();
  }, []);

  return connected;
}
