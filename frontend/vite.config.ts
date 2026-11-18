import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": process.env.B3_API_TARGET ?? "http://127.0.0.1:8000",
      "/health": process.env.B3_API_TARGET ?? "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "node",
    globals: true,
    include: ["src/**/*.test.ts"],
  },
});
