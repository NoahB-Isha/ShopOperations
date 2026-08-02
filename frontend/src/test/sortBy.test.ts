import { describe, expect, it } from "vitest";
import { sortBy, toggledSort } from "../pages/purchasing/orderingBits";

const rows = [
  { name: "banana", qty: 2 },
  { name: "Apple", qty: 30 },
  { name: "cherry", qty: 7 },
];
const value = (r: (typeof rows)[number], key: string) => (key === "name" ? r.name : r.qty);

describe("purchasing table sort", () => {
  it("returns rows unchanged (copied) with no sort", () => {
    const out = sortBy(rows, null, value);
    expect(out).toEqual(rows);
    expect(out).not.toBe(rows);
  });

  it("sorts strings a-z case-insensitively and numbers numerically", () => {
    expect(sortBy(rows, { key: "name", dir: "asc" }, value).map((r) => r.name)).toEqual([
      "Apple",
      "banana",
      "cherry",
    ]);
    // numeric, not lexicographic — 2 < 7 < 30
    expect(sortBy(rows, { key: "qty", dir: "asc" }, value).map((r) => r.qty)).toEqual([2, 7, 30]);
    expect(sortBy(rows, { key: "qty", dir: "desc" }, value).map((r) => r.qty)).toEqual([30, 7, 2]);
  });

  it("toggles asc → desc on the same column, resets to asc on a new one", () => {
    const first = toggledSort(null, "name");
    expect(first).toEqual({ key: "name", dir: "asc" });
    const second = toggledSort(first, "name");
    expect(second).toEqual({ key: "name", dir: "desc" });
    expect(toggledSort(second, "name")).toEqual({ key: "name", dir: "asc" });
    expect(toggledSort(second, "qty")).toEqual({ key: "qty", dir: "asc" });
  });
});
