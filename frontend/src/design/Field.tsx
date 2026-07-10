import type {
  InputHTMLAttributes,
  ReactNode,
  Ref,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

const controlBase =
  "w-full rounded-(--radius-sm) border border-line-strong bg-surface px-3 text-sm " +
  "text-ink placeholder:text-ink-faint transition-colors " +
  "hover:border-copper/50 focus:border-copper focus:outline-none " +
  "disabled:bg-raised disabled:text-ink-faint";

export function Input({
  className = "",
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { ref?: Ref<HTMLInputElement> }) {
  return <input className={`${controlBase} h-10 ${className}`} {...rest} />;
}

export function Textarea({
  className = "",
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`${controlBase} py-2 min-h-20 ${className}`} {...rest} />;
}

export function Select({
  className = "",
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={`${controlBase} h-10 pr-8 appearance-none bg-no-repeat bg-right ${className}`}
      style={{
        backgroundImage:
          "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' fill='none'%3E%3Cpath d='M4 6l4 4 4-4' stroke='%23948b78' stroke-width='1.5' stroke-linecap='round'/%3E%3C/svg%3E\")",
        backgroundPosition: "right 0.6rem center",
      }}
      {...rest}
    >
      {children}
    </select>
  );
}

export function Field({
  label,
  help,
  error,
  children,
  className = "",
}: {
  label: string;
  help?: string;
  error?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={`block ${className}`}>
      <span className="label-caps mb-1.5 block">{label}</span>
      {children}
      {error ? (
        <span className="mt-1 block text-[13px] text-danger">{error}</span>
      ) : help ? (
        <span className="mt-1 block text-[13px] text-ink-faint">{help}</span>
      ) : null}
    </label>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`inline-flex items-center gap-2 select-none disabled:opacity-50`}
    >
      <span
        className={`relative h-5.5 w-9.5 rounded-full transition-colors duration-150
          ${checked ? "bg-forest" : "bg-line-strong"}`}
      >
        <span
          className={`absolute top-0.5 h-4.5 w-4.5 rounded-full bg-surface shadow-soft
            transition-transform duration-150 ${checked ? "translate-x-4.5" : "translate-x-0.5"}`}
        />
      </span>
      {label && <span className="text-sm text-ink-soft">{label}</span>}
    </button>
  );
}
