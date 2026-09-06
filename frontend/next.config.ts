import type { NextConfig } from "next";

// Browser talks only to this origin; /api/* is proxied server-side to the
// FastAPI backend so session cookies stay same-site (SameSite=Lax, HttpOnly).
const backend = process.env.EMS_BACKEND_URL;
if (!backend) {
  throw new Error("EMS_BACKEND_URL environment variable is required (e.g. https://ems-backend.onrender.com)");
}

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend.replace(/\/$/, "")}/api/:path*` }];
  },
};

export default nextConfig;
