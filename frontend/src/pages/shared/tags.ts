import type { BadgeTone } from "../../design";

export const TAG_LABELS: Record<string, string> = {
  air_only: "Air only",
  sea_only: "Sea only",
  gold: "Gold",
  silver: "Silver",
  bloom: "Bloom",
  camphor: "Camphor",
  toothpaste: "Toothpaste",
  expires: "Expires",
};

/** One tone per tag, no two alike — a tag is recognizable by color alone. */
export const TAG_TONES: Record<string, BadgeTone> = {
  air_only: "tertiary",
  sea_only: "forest",
  gold: "gold",
  silver: "outline",
  bloom: "secondary",
  camphor: "copper",
  toothpaste: "neutral",
  expires: "danger",
};
