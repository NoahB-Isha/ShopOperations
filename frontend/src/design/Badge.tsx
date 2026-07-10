import type { ReactNode } from "react";

export type BadgeTone = "neutral" | "copper" | "forest" | "gold" | "danger" | "outline";

const tones: Record<BadgeTone, string> = {
  neutral: "bg-raised text-ink-soft border border-line",
  copper: "bg-copper-tint text-copper-deep",
  forest: "bg-forest-tint text-forest-deep",
  gold: "bg-gold-tint text-gold",
  danger: "bg-danger-tint text-danger",
  outline: "border border-line-strong text-ink-faint",
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
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px]
        font-medium leading-4 whitespace-nowrap ${tones[tone]}`}
    >
      {children}
    </span>
  );
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
  const color = ok ? "bg-forest" : warn ? "bg-gold" : "bg-danger";
  return (
    <span className="inline-flex items-center gap-1.5 text-[13px] text-ink-soft">
      <span className={`h-2 w-2 rounded-full ${color}`} />
      {label}
    </span>
  );
}
