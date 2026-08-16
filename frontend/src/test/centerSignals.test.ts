import { describe, expect, it } from "vitest";
import {
  R_MAX,
  R_MIN,
  R_QUIET,
  monthName,
  radiusFor,
  trendOf,
  trendPct,
} from "../pages/admin/centerSignals";

describe("dot sizing", () => {
  it("scales by AREA, not radius", () => {
    // four times the sales should be twice the radius above the floor, so the
    // ink grows with the number rather than with its square
    const max = 400;
    const quarter = radiusFor(100, max) - R_MIN;
    const full = radiusFor(400, max) - R_MIN;
    expect(full / quarter).toBeCloseTo(2, 5);
    expect(radiusFor(max, max)).toBeCloseTo(R_MAX, 5);
  });

  it("still draws a center that sold nothing, or that the rollup never saw", () => {
    expect(radiusFor(0, 400)).toBe(R_QUIET);
    expect(radiusFor(null, 400)).toBe(R_QUIET);
    expect(radiusFor(50, 0)).toBe(R_QUIET); // no scale to compare against
  });
});

describe("month-over-month trend", () => {
  const sig = (sales_units: number | null, sales_prev_units: number | null) => ({
    sales_units,
    sales_prev_units,
  });

  it("reads the direction of the last two setups", () => {
    expect(trendOf(sig(120, 80))).toBe("up");
    expect(trendOf(sig(80, 120))).toBe("down");
    expect(trendPct(sig(120, 80))).toBe(50);
    expect(trendPct(sig(80, 120))).toBe(-33);
  });

  it("calls a small wobble flat rather than a story", () => {
    expect(trendOf(sig(102, 100))).toBe("flat");
    expect(trendOf(sig(96, 100))).toBe("flat");
    expect(trendOf(sig(106, 100))).toBe("up");
  });

  it("separates a first month from no data and from a real zero", () => {
    expect(trendOf(sig(30, 0))).toBe("first"); // sold, nothing to compare
    expect(trendOf(sig(0, 0))).toBe("none");
    expect(trendOf(sig(null, null))).toBe("none"); // never seen by the rollup
    expect(trendPct(sig(30, 0))).toBeNull(); // no dividing by a zero baseline
  });
});

describe("month labels", () => {
  it("names the month, and stays vague rather than lying", () => {
    expect(monthName("2026-07")).toBe("July");
    expect(monthName("")).toBe("last month");
    expect(monthName("nonsense")).toBe("last month");
  });
});
