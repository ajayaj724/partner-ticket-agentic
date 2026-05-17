import type { NextConfig } from "next";

const FASTAPI_ORIGIN = process.env.FASTAPI_ORIGIN ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // Proxy /api/* to the Python FastAPI backend so the frontend can use
  // same-origin fetches and CORS is a non-issue. In production you'd
  // reverse-proxy the same way at the edge.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${FASTAPI_ORIGIN}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
