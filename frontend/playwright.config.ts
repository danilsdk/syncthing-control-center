import { defineConfig, devices } from "@playwright/test";

const liveURL = process.env.B4_LIVE_URL;
if (liveURL && !/^http:\/\/127\.0\.0\.1:[0-9]+\/?$/.test(liveURL)) {
  throw new Error("B4_LIVE_URL must address the disposable loopback A4 stand");
}

export default defineConfig({
  testDir: "./e2e",
  testIgnore:
    liveURL || process.env.B3_REAL_API
      ? []
      : ["**/api.spec.ts", "**/b4-live.spec.ts"],
  testMatch: liveURL
    ? "**/b4-live.spec.ts"
    : process.env.B3_REAL_API
      ? "**/api.spec.ts"
      : "**/*.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  use: {
    ...devices["Desktop Chrome"],
    channel: "chromium",
    baseURL: liveURL ?? "http://127.0.0.1:4173",
    trace: liveURL ? "off" : "retain-on-failure",
    screenshot: liveURL ? "off" : "only-on-failure",
  },
  outputDir: "./test-results",
  webServer: liveURL
    ? undefined
    : {
        command: "npm run dev -- --host 127.0.0.1 --port 4173 --strictPort",
        url: "http://127.0.0.1:4173",
        reuseExistingServer: false,
        env: { VITE_DATA_MODE: "api" },
      },
});
