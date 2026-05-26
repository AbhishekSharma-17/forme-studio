/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Surfaced so server-side `fetch` from RSCs hits the same backend the
  // client uses. NEXT_PUBLIC_* is read by the browser; FORME_BACKEND_URL
  // (server only) overrides for SSR.
  env: {
    NEXT_PUBLIC_BACKEND_URL:
      process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://127.0.0.1:8002",
  },
};

export default nextConfig;
