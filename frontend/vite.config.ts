import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the UI runs on :5173 and forwards /api to the FastAPI server on 8077 (8000 is often taken).
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8077" } },
});
