import type { ReactNode } from "react";

export function Card({
  children,
  className = "",
  pad = true,
}: {
  children: ReactNode;
  className?: string;
  pad?: boolean;
}) {
  return (
    <div
      className={`rounded-(--radius-md) border border-line bg-surface shadow-soft
        ${pad ? "p-5" : ""} ${className}`}
    >
      {children}
    </div>
  );
}

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
    default: "text-ink",
    good: "text-forest-deep",
    warn: "text-gold",
    bad: "text-danger",
  };
  return (
    <Card className="min-w-40">
      <div className="label-caps">{label}</div>
      <div className={`display mt-1 text-2xl ${tones[tone]}`}>{value}</div>
      {hint && <div className="mt-1 text-[13px] text-ink-faint">{hint}</div>}
    </Card>
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
        <h1 className="display text-[26px] leading-8">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-ink-faint">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
