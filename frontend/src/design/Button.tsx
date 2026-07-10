import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Spinner } from "./Spinner";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const styles: Record<Variant, string> = {
  primary:
    "bg-copper text-surface hover:bg-copper-deep active:bg-copper-deep shadow-soft " +
    "disabled:bg-line-strong disabled:text-ink-faint disabled:shadow-none",
  secondary:
    "bg-surface text-ink border border-line-strong hover:border-copper hover:text-copper-deep " +
    "disabled:text-ink-faint disabled:hover:border-line-strong",
  ghost: "text-ink-soft hover:bg-copper-tint hover:text-copper-deep disabled:text-ink-faint",
  danger:
    "bg-danger text-surface hover:brightness-95 disabled:bg-line-strong disabled:text-ink-faint",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-[13px] gap-1.5",
  md: "h-10 px-4 text-sm gap-2",
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
      className={`inline-flex items-center justify-center rounded-(--radius-sm) font-medium
        transition-colors duration-100 select-none whitespace-nowrap
        ${styles[variant]} ${sizes[size]} ${className}`}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Spinner size={14} /> : icon}
      {children}
    </button>
  );
}
