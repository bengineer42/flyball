import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The daemon serves the API on 8000; the dev server proxies so the browser
// makes same-origin requests and CORS never enters into it.
const DAEMON = process.env.HUMCTRL_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: DAEMON, changeOrigin: true },
      "/ws": { target: DAEMON, ws: true, changeOrigin: true },
    },
  },
});
