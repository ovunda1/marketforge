// End-to-end tests (M11): headless Chromium (Playwright's own, never a local Chrome)
// against a running compose stack's dashboard: `make e2e` brings up a fresh test stack.
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1, // one shared exchange; the restart test would disturb parallel tests
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    ...devices["Desktop Chrome"],
    channel: undefined,
    headless: true,
    baseURL: process.env.MF_DASHBOARD_URL ?? "http://127.0.0.1:8080",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
