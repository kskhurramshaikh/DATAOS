import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Base "/dashboard/" -- app/main.py serves this app's index.html at
// GET /dashboard, and its built assets at /dashboard/assets/*, so every
// asset URL the build emits needs that prefix baked in.
export default defineConfig({
  plugins: [react()],
  base: "/dashboard/",
  build: {
    outDir: "dist",
  },
});
