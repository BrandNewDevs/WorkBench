import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const projectRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  build: {
    ssr: true,
    outDir: resolve(projectRoot, "dist/electron/main"),
    emptyOutDir: true,
    lib: {
      entry: resolve(projectRoot, "src/main/index.ts"),
      formats: ["es"],
      fileName: () => "index.js",
    },
    rollupOptions: {
      external: ["electron", /^node:/],
    },
    sourcemap: true,
  },
});
