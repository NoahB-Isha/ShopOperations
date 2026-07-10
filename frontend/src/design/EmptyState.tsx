import type { ReactNode } from "react";

export function EmptyState({
  icon,
  title,
  hint,
  action,
}: {
  icon?: ReactNode;
  title: string;
  hint?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-(--radius-md) border
      border-dashed border-line-strong bg-raised/60 px-8 py-14 text-center">
      {icon && <div className="mb-3 text-ink-faint">{icon}</div>}
      <div className="display text-lg text-ink-soft">{title}</div>
      {hint && <div className="mt-1.5 max-w-sm text-sm text-ink-faint">{hint}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
