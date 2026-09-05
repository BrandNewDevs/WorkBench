import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const projectRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  build: {
    ssr: true,
    outDir: resolve(projectRoot, "dist/electron/preload"),
    emptyOutDir: true,
    lib: {
      entry: resolve(projectRoot, "src/preload/index.ts"),
      formats: ["cjs"],
      fileName: () => "index.cjs",
    },
    rollupOptions: {
      external: ["electron", /^node:/],
    },
    sourcemap: true,
  },
});
