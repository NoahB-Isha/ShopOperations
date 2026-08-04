import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { clearPersisted, usePersistedState } from "../persist";

describe("usePersistedState", () => {
  beforeEach(() => sessionStorage.clear());

  it("survives unmount and remount (menu navigation)", () => {
    const a = renderHook(() => usePersistedState("t.search", ""));
    act(() => a.result.current[1]("copper bottle"));
    a.unmount();

    const b = renderHook(() => usePersistedState("t.search", ""));
    expect(b.result.current[0]).toBe("copper bottle");
  });

  it("re-seeds when the key changes (per-entity state)", () => {
    const { result, rerender } = renderHook(
      ({ k }) => usePersistedState<Record<string, number>>(k, {}),
      { initialProps: { k: "cart.1" } },
    );
    act(() => result.current[1]({ 42: 3 }));

    rerender({ k: "cart.2" });
    expect(result.current[0]).toEqual({}); // center 2 starts clean

    act(() => result.current[1]({ 7: 1 }));
    rerender({ k: "cart.1" });
    expect(result.current[0]).toEqual({ 42: 3 }); // center 1's cart intact
  });

  it("supports functional updates and ignores corrupt storage", () => {
    sessionStorage.setItem("t.n", "{not json");
    const { result } = renderHook(() => usePersistedState("t.n", 10));
    expect(result.current[0]).toBe(10); // corrupt entry → initial
    act(() => result.current[1]((n) => n + 5));
    expect(result.current[0]).toBe(15);
    expect(JSON.parse(sessionStorage.getItem("t.n")!)).toBe(15);
  });

  it("clearPersisted removes keys for submit flows", () => {
    const { result, unmount } = renderHook(() => usePersistedState("t.lines", [1, 2]));
    act(() => result.current[1]([3]));
    unmount();
    clearPersisted("t.lines");
    const fresh = renderHook(() => usePersistedState<number[]>("t.lines", []));
    expect(fresh.result.current[0]).toEqual([]);
  });
});
