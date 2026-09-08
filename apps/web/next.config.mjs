/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Required by the Docker runner stage: bundles a minimal server.
  output: "standalone",
  // Proxy to the FastAPI service so the browser makes same-origin
  // requests: no CORS preflight, and no API URL baked into the bundle.
  async rewrites() {
    const api = process.env.NETRA_API_URL || "http://127.0.0.1:8000";
    return [
      { source: "/api/:path*", destination: `${api}/api/:path*` },
      // Evidence crops and the live-wall frames are served by the API.
      { source: "/evidence/:path*", destination: `${api}/evidence/:path*` },
    ];
  },
};
export default nextConfig;
