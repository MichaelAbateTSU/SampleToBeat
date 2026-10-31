import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));

// The E2E suite runs the real backend (serving the built SPA) against a throwaway data directory.
const port = Number(process.env.E2E_PORT ?? 8799);
const dataDir = process.env.E2E_DATA ?? mkdtempSync(join(tmpdir(), "choplab-e2e-"));

export default defineConfig({
  testDir: "./e2e",
  timeout: 10 * 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    acceptDownloads: true,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // e.g. PW_CHANNEL=msedge or chrome to use an installed browser instead of `npx playwright install chromium`
    channel: process.env.PW_CHANNEL || undefined,
    launchOptions: { args: ["--autoplay-policy=no-user-gesture-required"] },
  },
  webServer: {
    command: "uv run choplab",
    cwd: resolve(here, "../backend"),
    url: `http://127.0.0.1:${port}/api/health`,
    timeout: 180_000,
    reuseExistingServer: false,
    env: {
      CHOPLAB_PORT: String(port),
      CHOPLAB_DATA: dataDir,
      CHOPLAB_FRONTEND: resolve(here, "dist"),
    },
  },
});
