/**
 * Preflight: the e2e suites place orders, approve them, and send transfer
 * requests — flows that render Odoo drafts and dispatch notifications. With
 * the write/notify/ordering flags OFF (their shipped state) all of that is
 * safely simulated; with any of them ON, a test run writes REAL records into
 * production Odoo and can send real messages.
 *
 * That must never happen by accident again (it did on 2026-07-20/21, after
 * the flags were enabled on the shared stack) — so the whole suite refuses
 * to start unless every live flag is off.
 */
import { request } from "@playwright/test";

export default async function globalSetup() {
  const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:5173";
  const ctx = await request.newContext({ baseURL });
  try {
    const codeResp = await ctx.post("/api/v1/auth/request-code", {
      data: { identifier: "admin@demo.ishalife.test" },
    });
    if (!codeResp.ok()) {
      throw new Error(
        `stack not reachable/seeded at ${baseURL} (auth request-code → ${codeResp.status()})`,
      );
    }
    const { dev_code } = await codeResp.json();
    if (!dev_code) {
      throw new Error("dev auth mode is off — e2e needs the seeded dev stack");
    }
    const { token } = await (
      await ctx.post("/api/v1/auth/verify", {
        data: { identifier: "admin@demo.ishalife.test", code: dev_code },
      })
    ).json();
    const flagsResp = await ctx.get("/api/v1/admin/flags", {
      headers: { Authorization: `Bearer ${token}` },
    });
    const payload = await flagsResp.json();
    const flags: { key: string; enabled: boolean }[] = Array.isArray(payload)
      ? payload
      : (payload.flags ?? []);
    const live = flags.filter(
      (f) => f.enabled && (f.key.startsWith("write_") || f.key.endsWith("_live")),
    );
    if (live.length > 0) {
      throw new Error(
        `REFUSING TO RUN E2E: live flags are enabled on this stack — ${live
          .map((f) => f.key)
          .join(", ")}.\n` +
          "These suites create orders and transfers; with those flags on they would " +
          "write REAL draft records into production Odoo (and may send real messages).\n" +
          "Point E2E_BASE_URL at a stack with every write/notify/ordering flag OFF, " +
          "or turn the flags off for the test window.",
      );
    }
  } finally {
    await ctx.dispose();
  }
}
