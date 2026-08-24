import { describe, expect, it } from "vitest";
import { withNewestFirst } from "../transferDraft";
import type { PickedLine } from "../pages/shared/OpsBits";

const line = (product_id: number, qty = 1): PickedLine =>
  ({ product_id, qty, name: `p${product_id}`, sku: `SKU${product_id}` }) as PickedLine;

describe("withNewestFirst", () => {
  it("puts the item you just added at the top", () => {
    const draft = [line(1), line(2)];
    expect(withNewestFirst(draft, line(3)).map((l) => l.product_id)).toEqual([3, 1, 2]);
  });

  it("merges a repeat instead of duplicating it — the API rejects duplicates", () => {
    const draft = [line(1, 2), line(2, 5)];
    const next = withNewestFirst(draft, line(2, 3));
    expect(next.map((l) => l.product_id)).toEqual([2, 1]);
    expect(next[0].qty).toBe(8);
  });

  it("leaves the original array alone", () => {
    const draft = [line(1)];
    withNewestFirst(draft, line(2));
    expect(draft.map((l) => l.product_id)).toEqual([1]);
  });
});
