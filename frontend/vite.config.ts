import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base: the production build is mounted by FastAPI at /app (see api.py), so
// asset URLs must be absolute from there. One process serves API and UI.
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  build: { outDir: "dist", emptyOutDir: true },
});
