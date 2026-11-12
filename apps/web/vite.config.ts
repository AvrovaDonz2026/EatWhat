import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { viteSingleFile } from "vite-plugin-singlefile";
import { fileURLToPath } from "url";

// 即拿即用：默认打包成单文件 dist/index.html，双击或用任意静态站点托管即可运行。
const singleFile = process.env.PPD_SPLIT !== "1";

export default defineConfig({
  plugins: [react(), tailwindcss(), ...(singleFile ? [viteSingleFile({ removeViteModuleLoader: true })] : [])],
  base: "./",
  build: {
    target: "es2022",
    outDir: "dist",
    emptyOutDir: true,
    assetsInlineLimit: singleFile ? 100000000 : 4096,
    chunkSizeWarningLimit: 8192,
    cssCodeSplit: !singleFile,
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    fs: { strict: false },
  },
});
