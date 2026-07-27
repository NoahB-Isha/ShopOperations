/* Palette switching: pure presentation. The choice lives in localStorage and
   as `data-palette` on <html> (applied pre-paint by index.html); tokens.css
   maps each id to a full light scheme. Dark mode is one global scheme and
   follows the system — the palette shapes light mode only. */

export interface PaletteOption {
  id: string;
  label: string;
  /** secondary hue swatch shown in the picker (primary is identical in all) */
  dot: string;
}

export const PALETTES: PaletteOption[] = [
  { id: "pop", label: "Charcoal Pop", dot: "#b90d6e" },
  { id: "neem", label: "Neem Tree", dot: "#5c4f26" },
  { id: "turmeric", label: "Turmeric Root", dot: "#f5bd45" },
];

const STORAGE_KEY = "ilops_palette";
export const DEFAULT_PALETTE = "pop";

export function currentPalette(): string {
  return document.documentElement.dataset.palette || DEFAULT_PALETTE;
}

export function setPalette(id: string): void {
  document.documentElement.dataset.palette = id;
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    /* private browsing — the choice just won't persist */
  }
}
