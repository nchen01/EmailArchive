import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, /api requests are proxied to the FastAPI server so the browser
// never makes a cross-origin request (no CORS config needed on the API).
//
// port + strictPort: the dev server is pinned to 5173 and will FAIL LOUDLY if
// that port is already in use rather than silently moving to 5174. A
// deterministic URL keeps operator validation instructions simple; a port
// conflict now surfaces as an obvious error telling the operator to Ctrl+C the
// old `npm run dev` window before starting a new one.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
