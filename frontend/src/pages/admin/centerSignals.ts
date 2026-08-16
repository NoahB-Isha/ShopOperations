/* Pure signal maths for the centers map: how big a dot is and which way it
   points. Kept out of CentersMap.tsx because exporting non-components from a
   component module breaks Fast Refresh — the map's wheel and drag handlers
   silently stopped updating mid-session until a hard reload, which is a
   miserable thing to debug. It also makes these unit-testable. */

/** Dot sizing. AREA is proportional to units, not radius: doubling the radius
 *  quadruples the ink, and a reader would see four times the sales. R_QUIET is
 *  a center the rollup has never seen, or one that sold nothing — still drawn,
 *  because "exists and did nothing" is information. */
export const R_QUIET = 3.4;
export const R_MIN = 4.2;
export const R_MAX = 15;

export function radiusFor(units: number | null, max: number): number {
  if (!units || units <= 0 || max <= 0) return R_QUIET;
  return R_MIN + (R_MAX - R_MIN) * Math.sqrt(units / max);
}

export type Trend = "up" | "down" | "flat" | "first" | "none";

export interface SalesSignal {
  sales_units: number | null;
  sales_prev_units: number | null;
}

/** Month over month, which for a pop-up that sets up once a month is one
 *  setup against the previous one. A move under 5% is noise, not a story. */
export function trendOf(c: SalesSignal): Trend {
  if (c.sales_units == null) return "none";
  const now = c.sales_units;
  const before = c.sales_prev_units ?? 0;
  if (before === 0) return now > 0 ? "first" : "none";
  const change = (now - before) / before;
  if (Math.abs(change) < 0.05) return "flat";
  return change > 0 ? "up" : "down";
}

/** Percent change, or null when there is no baseline to divide by. */
export function trendPct(c: SalesSignal): number | null {
  if (c.sales_units == null || !c.sales_prev_units) return null;
  return Math.round(((c.sales_units - c.sales_prev_units) / c.sales_prev_units) * 100);
}

/** "2026-07" -> "July". Anything unparseable stays vague rather than becoming
 *  "Invalid Date" in the legend. */
export function monthName(bucket: string | undefined): string {
  if (!bucket) return "last month";
  const [y, m] = bucket.split("-").map(Number);
  if (!y || !m) return "last month";
  return new Date(y, m - 1, 1).toLocaleDateString([], { month: "long" });
}


/* ---------------------------------------------------------------- zones */

/** The four validated field-zone hues (see the --zone-* block in tokens.css:
 *  a dot map is an all-pairs form, which caps the safe set at four). */
export const ZONE_VARS = ["var(--zone-1)", "var(--zone-2)", "var(--zone-3)", "var(--zone-4)"];
export const NO_ZONE_COLOR = "var(--color-outline)";

export function isCampusZone(zoneName: string | null | undefined): boolean {
  return (zoneName ?? "").toLowerCase().includes("department");
}
export function isCanadaZone(zoneName: string | null | undefined): boolean {
  return (zoneName ?? "").toLowerCase() === "canada";
}

/**
 * Zone name -> hue, assigned from the FIELD zones in name order.
 *
 * Keyed on the name and derived from the full set, so filtering the list can
 * never repaint the survivors — colour follows the entity, not its rank. The
 * map and the list call this same function, which is the point: a dot and its
 * row wear the same colour.
 */
export function zoneColors(zoneNames: (string | null)[]): Map<string, string> {
  const field = [
    ...new Set(
      zoneNames.filter(
        (z): z is string => !!z && !isCanadaZone(z) && !isCampusZone(z),
      ),
    ),
  ].sort();
  return new Map(field.map((name, i) => [name, ZONE_VARS[i % ZONE_VARS.length]]));
}

/** The swatch for a zone: field zones get their hue, Canada and the campus get
 *  a ring (they are told apart by position and glyph on the map, not colour),
 *  and no zone gets grey. */
export function zoneSwatch(zoneName: string | null, colors: Map<string, string>) {
  if (!zoneName) return { color: NO_ZONE_COLOR, hollow: true };
  const hue = colors.get(zoneName);
  if (hue) return { color: hue, hollow: false };
  return { color: NO_ZONE_COLOR, hollow: !isCampusZone(zoneName) };
}
