import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, /api and /ws go to the daemon so the app is same-origin, as it is
// when the daemon serves the built bundle. Override with FLYBALL_URL; a daemon
// started with --root-path needs the same prefix here (FLYBALL_ROOT_PATH=/humidity),
// since the dev app sits at the root and asks for /api while the daemon answers
// only under /humidity/api.
const daemon = process.env.FLYBALL_URL ?? "http://127.0.0.1:8000";
const prefix = (process.env.FLYBALL_ROOT_PATH ?? "").replace(/\/$/, "");
const to = (ws = false) => ({ target: daemon, ws, rewrite: (path: string) => prefix + path });

export default defineConfig({
  plugins: [react()],
  resolve: { conditions: ["development"] },
  server: {
    // Poll rather than trust inotify: files rewritten by tools (rename-replace,
    // git stash) have repeatedly left Vite serving a stale or empty module.
    watch: { usePolling: true, interval: 300 },
    proxy: {
      "/api": to(),
      "/ws": to(true),
      "/mcp": to(),
    },
  },
  // Relative asset URLs: one build serves at the root or under any sub-path
  // (a daemon's `--root-path`); the app finds its API from where it was served.
  base: "./",
  build: { outDir: "dist", emptyOutDir: true },
});
