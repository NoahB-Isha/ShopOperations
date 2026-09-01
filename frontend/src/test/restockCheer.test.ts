import { describe, expect, it } from "vitest";
import {
  groupJustFinished,
  listJustFinished,
  milestoneFor,
} from "../pages/restock/restockCheer";

const row = (line_id: number, group: string, checked = false) => ({ line_id, group, checked });

describe("milestoneFor", () => {
  it("fires exactly on the thresholds", () => {
    expect(milestoneFor(100)).toBe(100);
    expect(milestoneFor(1000)).toBe(1000);
  });
  it("stays quiet everywhere else", () => {
    expect(milestoneFor(99)).toBeNull();
    expect(milestoneFor(101)).toBeNull();
    expect(milestoneFor(0)).toBeNull();
    expect(milestoneFor(null)).toBeNull();
    expect(milestoneFor(undefined)).toBeNull();
  });
});

describe("groupJustFinished", () => {
  it("names the aisle when this tick completes it", () => {
    const rows = [row(1, "Incense", true), row(2, "Incense"), row(3, "Copper")];
    expect(groupJustFinished(rows, 2)).toBe("Incense");
  });
  it("is quiet while aisle-mates remain", () => {
    const rows = [row(1, "Incense"), row(2, "Incense"), row(3, "Copper")];
    expect(groupJustFinished(rows, 2)).toBeNull();
  });
  it("blank groups celebrate together as Other", () => {
    const rows = [row(1, "", true), row(2, "")];
    expect(groupJustFinished(rows, 2)).toBe("Other");
  });
  it("a single-item aisle stays quiet — the list fanfare covers it", () => {
    expect(groupJustFinished([row(1, "Copper"), row(2, "Incense", true)], 1)).toBeNull();
  });
  it("unknown line ids never celebrate", () => {
    expect(groupJustFinished([row(1, "Incense")], 99)).toBeNull();
  });
});

describe("listJustFinished", () => {
  it("true when this tick is the last unchecked row", () => {
    const rows = [row(1, "A", true), row(2, "B")];
    expect(listJustFinished(rows, 2)).toBe(true);
  });
  it("false with work remaining, and false for an empty list", () => {
    expect(listJustFinished([row(1, "A"), row(2, "B")], 2)).toBe(false);
    expect(listJustFinished([], 1)).toBe(false);
  });
});
