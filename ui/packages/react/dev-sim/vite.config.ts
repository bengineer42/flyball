import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Scratch harness for the Simulation page against a live rig. Not part of
// the build; `npx vite packages/react/dev-sim --port 5182 --strictPort`.
const runner = process.env.FLYBALL_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  resolve: { conditions: ["development"] },
  server: {
    proxy: {
      "/api": runner,
      "/ws": { target: runner, ws: true },
    },
  },
});
