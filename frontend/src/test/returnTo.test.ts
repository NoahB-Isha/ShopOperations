import { beforeEach, describe, expect, it } from "vitest";
import { rememberReturnTo, safeReturnPath, takeReturnTo } from "../auth/returnTo";

describe("safeReturnPath", () => {
  it("keeps same-origin paths, with query and hash", () => {
    expect(safeReturnPath("/order?center=7")).toBe("/order?center=7");
    expect(safeReturnPath("/restock#top")).toBe("/restock#top");
  });

  it("refuses anything that could name another host", () => {
    // this value decides where a signed-in session lands — an open redirect
    // straight after login is exactly how a scanned QR becomes a phishing page
    for (const bad of [
      "//evil.example/order",
      "https://evil.example",
      "http://evil.example",
      "/\\evil.example",
      "javascript:alert(1)",
      "order",
      "",
      null,
      undefined,
    ]) {
      expect(safeReturnPath(bad)).toBeNull();
    }
  });

  it("refuses the login page itself — no redirect loops", () => {
    expect(safeReturnPath("/login")).toBeNull();
    expect(safeReturnPath("/login?next=/order")).toBeNull();
  });
});

describe("remember / take", () => {
  beforeEach(() => sessionStorage.clear());

  it("round-trips a destination exactly once", () => {
    rememberReturnTo("/order?center=7");
    expect(takeReturnTo()).toBe("/order?center=7");
    expect(takeReturnTo()).toBeNull(); // a stale destination can't hijack a later sign-in
  });

  it("never stores a rejected destination", () => {
    rememberReturnTo("https://evil.example");
    expect(takeReturnTo()).toBeNull();
  });
});
