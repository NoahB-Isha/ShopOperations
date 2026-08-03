import type { ReactNode } from "react";
import { useSillyLabel } from "../silly";

type CardVariant = "filled" | "elevated" | "outlined";
type CardTone = "none" | "primary" | "secondary" | "tertiary";

const variants: Record<CardVariant, string> = {
  filled: "bg-surface-container-low",
  elevated: "bg-surface-container-lowest shadow-(--shadow-e1)",
  outlined: "border border-outline-variant bg-surface",
};

const cardTones: Record<CardTone, string> = {
  none: "",
  primary: "bg-primary-container text-on-primary-container",
  secondary: "bg-secondary-container text-on-secondary-container",
  tertiary: "bg-tertiary-container text-on-tertiary-container",
};

export function Card({
  children,
  className = "",
  pad = true,
  variant = "filled",
  tone = "none",
}: {
  children: ReactNode;
  className?: string;
  pad?: boolean;
  variant?: CardVariant;
  tone?: CardTone;
}) {
  return (
    <div
      className={`rounded-(--radius-lg) ${tone === "none" ? variants[variant] : cardTones[tone]}
        ${pad ? "p-5" : ""} ${className}`}
    >
      {children}
    </div>
  );
}

/** Colorful tonal stat card — the status page's front row. Tones carry
 *  meaning: default=primary, good=success, warn=warn, bad=error containers. */
export function Stat({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "default" | "good" | "warn" | "bad";
}) {
  const tones = {
    default: "bg-primary-container text-on-primary-container",
    good: "bg-success-container text-on-success-container",
    warn: "bg-warn-container text-on-warn-container",
    bad: "bg-error-container text-on-error-container",
  };
  return (
    <div className={`min-w-40 rounded-(--radius-lg) p-5 ${tones[tone]}`}>
      <div className="text-xs font-bold tracking-wide uppercase opacity-75">{label}</div>
      <div className="display mt-1.5 text-[clamp(1.9rem,3vw,2.5rem)] leading-none">{value}</div>
      {hint && <div className="mt-1.5 text-[13px] opacity-75">{hint}</div>}
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  // silly mode renames known page titles; dynamic titles pass through
  const s = useSillyLabel();
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="display-l text-on-surface">{s(title)}</h1>
        {subtitle && (
          <p className="mt-2 text-[15px] text-on-surface-variant">
            {typeof subtitle === "string" ? s(subtitle) : subtitle}
          </p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
