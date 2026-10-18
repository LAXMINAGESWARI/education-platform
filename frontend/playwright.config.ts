import { defineConfig } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 240_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "off",
  },
  projects: [
    {
      name: "phone",
      use: {
        viewport: { width: 375, height: 812 },
        isMobile: true,
        hasTouch: true,
        deviceScaleFactor: 1,
      },
    },
    {
      name: "desktop",
      use: {
        viewport: { width: 1280, height: 800 },
        isMobile: false,
        hasTouch: false,
        deviceScaleFactor: 1,
      },
    },
  ],
});
