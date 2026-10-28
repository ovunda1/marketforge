/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

import { factsPlugin } from "./facts.config";

// The dev server proxies the gateway the way nginx does in the compose stack: /api/* to
// the API with the prefix stripped, /ws/* as WebSockets.
const api = process.env.MF_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), factsPlugin()],
  server: {
    fs: { allow: [".."] }, // results JSON lives in bench/, results/ and research/
    proxy: {
      "/api": { target: api, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
      "/ws": { target: api.replace(/^http/, "ws"), ws: true },
    },
  },
  test: { include: ["src/**/*.test.ts"] },
  build: { sourcemap: true },
});
