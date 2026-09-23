import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, /api and /ws go to the runner so the app is same-origin, as it is
// when the runner serves the built bundle. Override with FLYBALL_URL; a runner
// started with --root-path needs the same prefix here (FLYBALL_ROOT_PATH=/humidity),
// since the dev app sits at the root and asks for /api while the runner answers
// only under /humidity/api.
const runner = process.env.FLYBALL_URL ?? "http://127.0.0.1:8000";
const prefix = (process.env.FLYBALL_ROOT_PATH ?? "").replace(/\/$/, "");
const to = (ws = false) => ({ target: runner, ws, rewrite: (path: string) => prefix + path });

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
  // (a runner's `--root-path`); the app finds its API from where it was served.
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // The three heaviest, most stable third-party pieces get their own chunks: each
        // page's own React.lazy() split already keeps route code out of the entry bundle,
        // this keeps the vendor code that changes on its own release cadence out of it too,
        // so a rebuild after a dependency bump doesn't invalidate every page's chunk. Matched
        // by resolved path, not package name: the id-array form of manualChunks left "react"
        // an empty chunk (Vite/Rollup had already folded react/react-dom into other chunks
        // before the name match ran).
        manualChunks(id) {
          if (/node_modules\/(react|react-dom|scheduler)\//.test(id)) return "react";
          if (/node_modules\/(@mui|@emotion)\//.test(id)) return "mui";
          if (/node_modules\/uplot\//.test(id)) return "uplot";
        },
      },
    },
  },
});
