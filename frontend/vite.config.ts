import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { realpathSync } from "node:fs";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react()],
    optimizeDeps: { exclude: ["maplibre-gl"] },
    server: {
      port: 5173,
      allowedHosts: ["tuwebmx.anuarbarrera.dev"],
      fs: { allow: [process.cwd(), realpathSync("node_modules")] },
      proxy: {
        "/api": {
          target: env.VITE_API_PROXY_TARGET || "http://localhost:8000",
          changeOrigin: true,
        },
      },
    },
    build: { outDir: "dist", sourcemap: false },
  };
});
