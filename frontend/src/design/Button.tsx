import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Spinner } from "./Spinner";

/** M3 button styles. Legacy names kept so call sites don't change:
 *  primary = filled, secondary = filled tonal, ghost = text, danger = filled error. */
type Variant = "primary" | "secondary" | "ghost" | "danger" | "outlined" | "elevated";
type Size = "sm" | "md";

const styles: Record<Variant, string> = {
  primary:
    "bg-primary text-on-primary state-layer " +
    "disabled:bg-on-surface/10 disabled:text-on-surface/35",
  secondary:
    "bg-secondary-container text-on-secondary-container state-layer " +
    "disabled:bg-on-surface/10 disabled:text-on-surface/35",
  ghost: "text-primary state-layer disabled:text-on-surface/35",
  danger:
    "bg-error text-on-error state-layer disabled:bg-on-surface/10 disabled:text-on-surface/35",
  outlined:
    "border border-outline text-primary state-layer bg-transparent " +
    "disabled:border-on-surface/15 disabled:text-on-surface/35",
  elevated:
    "bg-surface-container-lowest text-primary shadow-(--shadow-e1) hover:shadow-(--shadow-e2) " +
    "state-layer disabled:bg-on-surface/10 disabled:text-on-surface/35 disabled:shadow-none",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-4 text-[13px] gap-1.5",
  md: "h-10 px-5 text-sm gap-2",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  icon?: ReactNode;
}

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  icon,
  children,
  disabled,
  className = "",
  ...rest
}: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center rounded-full font-medium
        transition-shadow duration-150 select-none whitespace-nowrap
        ${styles[variant]} ${sizes[size]} ${className}`}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Spinner size={14} className="text-current" /> : icon}
      {children}
    </button>
  );
}

/** M3 extended FAB — the page's one big friendly action. Position it yourself
 *  (usually fixed bottom-right). */
export function Fab({
  label,
  icon,
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; icon?: ReactNode }) {
  return (
    <button
      className={`state-layer inline-flex h-14 items-center gap-2.5 rounded-(--radius-lg)
        bg-tertiary-container px-5 text-sm font-semibold text-on-tertiary-container
        shadow-(--shadow-e2) transition-all duration-200 hover:shadow-(--shadow-e3)
        active:scale-[0.97] ${className}`}
      {...rest}
    >
      {icon ?? (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden>
          <path d="M9 3v12M3 9h12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      )}
      {label}
    </button>
  );
}
