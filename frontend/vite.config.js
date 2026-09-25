import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    host: "0.0.0.0",
    proxy: {
      // 平台后端（8000）
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      // AI 助手（5001）—— 去掉 /ai 前缀
      "/ai": {
        target: "http://127.0.0.1:5001",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/ai/, ""),
      },
    },
  },
});