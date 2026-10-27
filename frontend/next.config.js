/** @type {import('next').NextConfig} */
const backendUrl = process.env.BACKEND_URL || "http://127.0.0.1:8765";

const nextConfig = {
  output: "standalone",
  allowedDevOrigins: ["*.e2b.app"],
  skipTrailingSlashRedirect: true,
  // Lint is run separately in CI; legacy pages produce many style warnings.
  eslint: { ignoreDuringBuilds: true },
  async rewrites() {
    return [
      { source: "/socket.io", destination: `${backendUrl}/socket.io/` },
      {
        source: "/socket.io/:path*",
        destination: `${backendUrl}/socket.io/:path*`,
      },
      {
        source: "/api/youtube/:path*",
        destination: `${backendUrl}/api/youtube/:path*`,
      },
    ];
  },
};

export default nextConfig;
