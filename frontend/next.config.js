/** @type {import('next').NextConfig} */
const nextConfig = {
  // Two dev servers on one checkout overwrite each other's `.next` and start answering 404. A server
  // started with NEXT_DIST_DIR builds somewhere of its own; builds and deploys still use `.next`.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  output: "standalone",
  reactStrictMode: true,
  eslint: { ignoreDuringBuilds: true },
  typescript: { ignoreBuildErrors: true },
};
module.exports = nextConfig;
