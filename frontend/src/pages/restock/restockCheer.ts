/* Pure decision logic for the restock celebrations — which tick finishes an
   aisle, which finishes the list, which lifetime total is a milestone. Kept
   out of the component so it's unit-testable and Fast Refresh stays happy. */

export interface CheerRow {
  line_id: number;
  checked: boolean;
  group: string;
}

/** Lifetime totals that earn the big moment. The backend just reports the
 *  number; the thresholds are a UI opinion and live here alone. */
export const MILESTONES = [100, 250, 500, 1000, 2500, 5000, 10000];

export function milestoneFor(total: number | null | undefined): number | null {
  return total != null && MILESTONES.includes(total) ? total : null;
}

const label = (g: string) => g || "Other";

/** After ticking `lineId`, is its aisle now fully checked? Returns the group
 *  label for the toast, or null. Computed against the CURRENT rows with the
 *  tick applied locally — the refetch is still in flight when the sound has
 *  to play. Single-item groups stay quiet; the full-list fanfare covers a
 *  one-row list. */
export function groupJustFinished(rows: CheerRow[], lineId: number): string | null {
  const row = rows.find((r) => r.line_id === lineId);
  if (!row) return null;
  const aisle = rows.filter((r) => label(r.group) === label(row.group));
  if (aisle.length < 2) return null;
  return aisle.every((r) => r.line_id === lineId || r.checked) ? label(row.group) : null;
}

/** After ticking `lineId`, is the whole list done? */
export function listJustFinished(rows: CheerRow[], lineId: number): boolean {
  return rows.length > 0 && rows.every((r) => r.line_id === lineId || r.checked);
}
