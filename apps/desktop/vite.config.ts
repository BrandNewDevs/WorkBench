import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";

const projectRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  root: resolve(projectRoot, "src/renderer"),
  base: "./",
  resolve: {
    alias: {
      "@": resolve(projectRoot, "src/renderer"),
    },
  },
  plugins: [react(), tailwindcss()],
  build: {
    outDir: resolve(projectRoot, "dist/renderer"),
    emptyOutDir: true,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
  },
});
