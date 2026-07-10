import type { ReactNode } from "react";

/** M3 chips. Legacy tone names map onto container roles:
 *  copper→primary, forest→success, gold→warn, danger→error — plus the new
 *  secondary/tertiary for colorful-but-meaningless accents. */
export type BadgeTone =
  | "neutral"
  | "copper"
  | "forest"
  | "gold"
  | "danger"
  | "outline"
  | "secondary"
  | "tertiary";

const tones: Record<BadgeTone, string> = {
  neutral: "bg-surface-container-high text-on-surface-variant",
  copper: "bg-primary-container text-on-primary-container",
  forest: "bg-success-container text-on-success-container",
  gold: "bg-warn-container text-on-warn-container",
  danger: "bg-error-container text-on-error-container",
  outline: "border border-outline text-on-surface-variant",
  secondary: "bg-secondary-container text-on-secondary-container",
  tertiary: "bg-tertiary-container text-on-tertiary-container",
};

export function Badge({
  tone = "neutral",
  children,
  title,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded-(--radius-sm) px-2.5 py-[3px]
        text-[11.5px] font-semibold leading-4 whitespace-nowrap
        transition-transform duration-200 ease-(--ease-spring) hover:scale-110
        ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

/** Deterministic colorful tone for a label (e.g. category chips): the same
 *  string always lands on the same container color — fun AND scannable. */
const CYCLE: BadgeTone[] = ["copper", "secondary", "tertiary", "forest", "gold"];
export function toneForLabel(label: string): BadgeTone {
  let h = 0;
  for (const ch of label) h = (h * 31 + ch.charCodeAt(0)) % 997;
  return CYCLE[h % CYCLE.length];
}

/** Small status dot with label — used for sync health, active flags. */
export function StatusDot({
  ok,
  warn = false,
  label,
}: {
  ok: boolean;
  warn?: boolean;
  label?: string;
}) {
  const color = ok ? "bg-success" : warn ? "bg-warn" : "bg-error";
  return (
    <span className="inline-flex items-center gap-1.5 text-[13px] text-on-surface-variant">
      <span className={`h-2 w-2 rounded-full ${color}`} />
      {label}
    </span>
  );
}
