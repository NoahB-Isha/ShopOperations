import { matchesSearch } from "../search";

test("separator- and order-insensitive within a field", () => {
  expect(matchesSearch("Yoga mat", "Yoga-Mat-Cotton-Brown")).toBe(true);
  expect(matchesSearch("yoga-mat", "Yoga Mat Cotton")).toBe(true);
  expect(matchesSearch("mat yoga", "Yoga-Mat-Cotton-Brown")).toBe(true);
  expect(matchesSearch("copper bottle", "Copper Water Bottle — 950ml")).toBe(true);
  expect(matchesSearch("granite", "Yoga-Mat-Cotton-Brown")).toBe(false);
});

test("tokens must all land in one field — no cross-field mixing", () => {
  expect(matchesSearch("yoga 8901", "Yoga Mat", "8901234")).toBe(false);
  expect(matchesSearch("8901", "Yoga Mat", "8901234")).toBe(true);
});

test("empty and punctuation-only queries match everything", () => {
  expect(matchesSearch("", "anything")).toBe(true);
  expect(matchesSearch("  —- ", "anything")).toBe(true);
});

test("null/undefined/empty fields are skipped", () => {
  expect(matchesSearch("yoga", null, undefined, "", "yoga-mat")).toBe(true);
  expect(matchesSearch("yoga", null, "")).toBe(false);
});
