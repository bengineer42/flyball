import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, /api and /ws go to the daemon so the app is same-origin, as it is
// when the daemon serves the built bundle. Override with FLYBALL_URL.
const daemon = process.env.FLYBALL_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  resolve: { conditions: ["development"] },
  server: {
    // Poll rather than trust inotify: files rewritten by tools (rename-replace,
    // git stash) have repeatedly left Vite serving a stale or empty module.
    watch: { usePolling: true, interval: 300 },
    proxy: {
      "/api": daemon,
      "/ws": { target: daemon, ws: true },
      "/mcp": daemon,
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
