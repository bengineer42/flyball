import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Scratch harness for the controller, device-signals and events panels against a live rig. Not
// part of the build; `npx vite packages/react/dev --config packages/react/dev/vite.config.ts`.
const runner = process.env.FLYBALL_URL ?? "http://127.0.0.1:8001";

export default defineConfig({
  plugins: [react()],
  resolve: { conditions: ["development"] },
  server: {
    // Polling, as the app's config does: several Vite servers at once exhaust the inotify watch limit (ENOSPC).
    watch: { usePolling: true },
    proxy: {
      "/api": runner,
      "/ws": { target: runner, ws: true },
    },
  },
});
