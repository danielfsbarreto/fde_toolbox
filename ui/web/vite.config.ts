import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` (bin/start --dev) proxies the API and sign-in to the Python backend on :8765.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    allowedHosts: true, // reached through the ngrok domain in `bin/start --dev`
    proxy: {
      "/api": { target: "http://localhost:8765", xfwd: true },
      "/auth": { target: "http://localhost:8765", xfwd: true },
    },
  },
});
