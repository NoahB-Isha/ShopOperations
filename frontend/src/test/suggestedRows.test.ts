import { splitSlots } from "../pages/transfers/suggestedRows";

const asks = (n: number) => Array.from({ length: n }, (_, i) => `ask${i}`);
const sugg = (n: number) => Array.from({ length: n }, (_, i) => `sug${i}`);

test("asks take slots before suggestions", () => {
  const s = splitSlots(asks(2), sugg(10), 5);
  expect(s.asks).toEqual(["ask0", "ask1"]);
  expect(s.suggestions).toEqual(["sug0", "sug1", "sug2"]);
  expect(s.hidden).toBe(7);
  expect(s.total).toBe(12);
});

test("asks alone can fill every slot", () => {
  const s = splitSlots(asks(8), sugg(4), 5);
  expect(s.asks).toHaveLength(5);
  expect(s.suggestions).toEqual([]);
  expect(s.hidden).toBe(7);
});

test("a short list shows everything and hides nothing", () => {
  const s = splitSlots(asks(1), sugg(2), 5);
  expect(s.hidden).toBe(0);
  expect(s.asks.length + s.suggestions.length).toBe(3);
});

test("empty in, empty out — the strip renders nothing on a zero total", () => {
  const s = splitSlots([], [], 5);
  expect(s.total).toBe(0);
  expect(s.hidden).toBe(0);
});
