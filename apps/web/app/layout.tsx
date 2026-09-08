import type { Metadata, Viewport } from "next";
import "./globals.css";

/** Next.js App Router picks up app/icon.svg, app/apple-icon.svg and
 *  app/favicon.ico automatically and emits the correct <link> tags, so
 *  the icons are declared by file placement rather than by hand. */
export const metadata: Metadata = {
  title: {
    default: "NETRA — ANPR & Crime Tracking Platform",
    template: "%s · NETRA",
  },
  description:
    "City-wide automatic number plate recognition with spatio-temporal crime " +
    "detection. Plates are confirmed by temporal voting across many frames; " +
    "anything below the auto-accept threshold goes to a human rather than " +
    "being published as fact.",
  applicationName: "NETRA",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "NETRA Camera", statusBarStyle: "black-translucent" },
  keywords: ["ANPR", "ALPR", "number plate recognition", "traffic surveillance",
             "crime detection", "computer vision", "Smart India Hackathon"],
  authors: [{ name: "Team NETRA" }],
  openGraph: {
    title: "NETRA — ANPR & Crime Tracking Platform",
    description:
      "Reads number plates at near-perfect published accuracy and tracks " +
      "vehicle movement across a city.",
    siteName: "NETRA",
    type: "website",
  },
  // A surveillance console should never be indexed or previewed publicly.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#1d4ed8",
  colorScheme: "light",
  // The capture page is held in one hand; a stray pinch should not zoom
  // the viewfinder out from under the operator.
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-density="default">
      <body>{children}</body>
    </html>
  );
}
