import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  // refuses to run when write/notify flags are live — see e2e/global-setup.ts
  globalSetup: "./e2e/global-setup.ts",
  // flows share demo users and mutate real state — keep runs deterministic
  workers: 1,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    screenshot: "only-on-failure",
  },
  reporter: [["list"]],
});
