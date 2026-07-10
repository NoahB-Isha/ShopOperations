import type {
  InputHTMLAttributes,
  ReactNode,
  Ref,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

/* All control styling lives in tokens.css as `.m3-control` (component layer),
   so Tailwind utilities passed via className override cleanly. Bare controls
   are compact filled fields; inside <Field> they grow to the 56px floating-
   label anatomy. `--control-radius` lets call sites go full-pill. */

export function Input({
  className = "",
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { ref?: Ref<HTMLInputElement> }) {
  return <input placeholder=" " className={`m3-control ${className}`} {...rest} />;
}

export function Textarea({
  className = "",
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea placeholder=" " className={`m3-control ${className}`} {...rest} />;
}

export function Select({
  className = "",
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={`m3-control bg-right bg-no-repeat ${className}`}
      style={{
        backgroundImage:
          "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' fill='none'%3E%3Cpath d='M4 6l4 4 4-4' stroke='%23817567' stroke-width='1.8' stroke-linecap='round'/%3E%3C/svg%3E\")",
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
      <span className={`m3-field ${error ? "m3-field--error" : ""}`}>
        {children}
        <span className="m3-field-label">{label}</span>
      </span>
      {error ? (
        <span className="mt-1 block px-3.5 text-[13px] text-error">{error}</span>
      ) : help ? (
        <span className="mt-1 block px-3.5 text-[13px] text-on-surface-variant">{help}</span>
      ) : null}
    </label>
  );
}

/** M3 switch: chunky track, thumb that grows and gains a check when on. */
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
      className="inline-flex select-none items-center gap-2.5 disabled:opacity-40"
    >
      <span
        className={`relative h-7 w-12 shrink-0 rounded-full transition-colors duration-200
          ${checked ? "bg-primary" : "border-2 border-outline bg-surface-container-highest"}`}
      >
        <span
          className={`absolute top-1/2 grid -translate-y-1/2 place-items-center rounded-full
            transition-all duration-200 ease-(--ease-emphasized)
            ${
              checked
                ? "left-[calc(100%-1.5rem-2px)] h-6 w-6 bg-on-primary text-primary"
                : "left-1 h-4 w-4 bg-outline text-transparent"
            }`}
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden>
            <path
              d="M2.5 6.5 5 9l4.5-6"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
      </span>
      {label && <span className="text-sm text-on-surface-variant">{label}</span>}
    </button>
  );
}
