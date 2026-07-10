import type { ReactNode } from "react";

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
      <div className="text-xs font-semibold tracking-wide opacity-80">{label}</div>
      <div className="display mt-1 text-[26px] leading-8">{value}</div>
      {hint && <div className="mt-1 text-[13px] opacity-75">{hint}</div>}
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
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="display text-[28px] leading-9">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-on-surface-variant">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
