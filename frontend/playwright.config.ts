import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  testIgnore: process.env.B3_REAL_API ? [] : ["**/api.spec.ts"],
  testMatch: process.env.B3_REAL_API ? "**/api.spec.ts" : "**/*.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  use: {
    ...devices["Desktop Chrome"],
    channel: "chromium",
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  outputDir: "./test-results",
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: false,
    env: { VITE_DATA_MODE: "api" },
  },
});
