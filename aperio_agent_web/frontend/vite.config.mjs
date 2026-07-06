import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  root: resolve("aperio_agent_web/frontend"),
  base: "/static/react/",
  plugins: [react()],
  build: {
    outDir: resolve("aperio_agent_web/static/react"),
    emptyOutDir: true,
  },
});
