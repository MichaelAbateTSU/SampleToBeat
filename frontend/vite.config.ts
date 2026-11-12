import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs separately (uv run choplab); in production FastAPI serves dist/ itself.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: false } } },
  build: { outDir: "dist", sourcemap: false, target: "es2022" },
});
