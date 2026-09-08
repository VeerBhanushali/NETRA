"use client";

import { useEffect } from "react";
import { PageHeader } from "@/components/Shell";
import { ErrorState } from "@/components/ui";

/** Route-level error boundary for the whole console.
 *
 *  Next.js renders this instead of the dev error overlay when any page in
 *  the group throws. Without it, one failed fetch replaces the entire
 *  operator console with a stack trace.
 */
export default function ConsoleError({
  error, reset,
}: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // Keep it in the console for whoever is debugging, but never on screen.
    console.error("[netra] unhandled error in console route:", error);
  }, [error]);

  return (
    <>
      <PageHeader title="Something failed on this screen" />
      <div className="p-6">
        <div className="panel">
          <ErrorState error={error} onRetry={reset} />
        </div>
      </div>
    </>
  );
}
