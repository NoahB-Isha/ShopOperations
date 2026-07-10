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
    <div
      className="flex flex-col items-center justify-center rounded-(--radius-xl)
        bg-surface-container-low px-8 py-14 text-center"
    >
      <div
        className="animate-float mb-5 grid h-20 w-20 place-items-center rounded-(--radius-lg)
          bg-tertiary-container text-on-tertiary-container"
        aria-hidden
      >
        {icon ?? (
          <svg width="26" height="26" viewBox="0 0 26 26" fill="none">
            <path
              d="M13 3c1.5 4 3 5.5 7 7-4 1.5-5.5 3-7 7-1.5-4-3-5.5-7-7 4-1.5 5.5-3 7-7Z"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinejoin="round"
            />
          </svg>
        )}
      </div>
      <div className="headline text-on-surface">{title}</div>
      {hint && <div className="mt-2 max-w-sm text-sm text-on-surface-variant">{hint}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
