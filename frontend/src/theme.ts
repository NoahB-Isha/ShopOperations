/* Appearance: pure presentation, two independent choices.

   PALETTE (`data-palette`) shapes LIGHT mode — tokens.css maps each id to a
   full scheme. THEME (`data-theme`) is light or dark, and dark is one global
   scheme shared by every palette.

   Both live in localStorage and are applied to <html> before first paint by
   public/palette.js. Dark used to be a bare prefers-color-scheme media query,
   so a device set to dark forced dark on the user with no way to choose a
   light palette (Noah, 2026-08-16). It is a setting now, with "system" as the
   default so the old behaviour is still what you get out of the box. */

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


/* ------------------------------------------------------------------ theme */

export type ThemeMode = "system" | "light" | "dark";

export const THEME_MODES: { id: ThemeMode; label: string; hint: string }[] = [
  { id: "system", label: "Match my device", hint: "Follows your phone or computer" },
  { id: "light", label: "Light", hint: "Always light, whatever the device says" },
  { id: "dark", label: "Dark", hint: "Always dark" },
];

const THEME_KEY = "ilops_theme";
const media = () =>
  typeof window !== "undefined" && window.matchMedia
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;

export function currentThemeMode(): ThemeMode {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* private browsing — fall through to system */
  }
  return "system";
}

/** What is actually on screen right now, which for "system" depends on the OS. */
export function resolvedTheme(mode: ThemeMode = currentThemeMode()): "light" | "dark" {
  if (mode === "dark") return "dark";
  if (mode === "light") return "light";
  return media()?.matches ? "dark" : "light";
}

function paint(mode: ThemeMode): void {
  document.documentElement.dataset.theme = resolvedTheme(mode);
  // keep the browser chrome (status bar, address bar) in step
  const dark = document.documentElement.dataset.theme === "dark";
  document
    .querySelectorAll('meta[name="theme-color"]')
    .forEach((el) => el.setAttribute("content", dark ? "#131523" : "#fbfafd"));
}

export function setThemeMode(mode: ThemeMode): void {
  try {
    if (mode === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, mode);
  } catch {
    /* private browsing — the choice just won't persist */
  }
  paint(mode);
}

/** Follow the OS while the user is on "system" — someone whose phone flips at
 *  sunset should see the app flip with it, without a reload. */
export function watchSystemTheme(): () => void {
  const mq = media();
  if (!mq) return () => {};
  const onChange = () => {
    if (currentThemeMode() === "system") paint("system");
  };
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}
