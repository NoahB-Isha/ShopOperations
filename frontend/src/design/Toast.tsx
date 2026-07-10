import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

type ToastKind = "success" | "error" | "info";
interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
}

const ToastContext = createContext<(kind: ToastKind, message: string) => void>(() => {});

export function useToast() {
  const push = useContext(ToastContext);
  return useMemo(
    () => ({
      success: (m: string) => push("success", m),
      error: (m: string) => push("error", m),
      info: (m: string) => push("info", m),
    }),
    [push],
  );
}

/* M3 snackbars: inverse surface, bottom-left, one colored dot of feeling. */
const dotColor: Record<ToastKind, string> = {
  success: "bg-success-container",
  error: "bg-[#ffb4ab]",
  info: "bg-inverse-primary",
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const push = useCallback((kind: ToastKind, message: string) => {
    const id = nextId.current++;
    setToasts((t) => [...t, { id, kind, message }]);
    window.setTimeout(
      () => setToasts((t) => t.filter((x) => x.id !== id)),
      kind === "error" ? 6500 : 3800,
    );
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      {/* bottom-24 on phones keeps snackbars clear of the bottom navigation bar */}
      <div className="pointer-events-none fixed bottom-24 left-4 z-50 flex w-[min(22rem,calc(100vw-2rem))] flex-col gap-2 md:bottom-4">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className="animate-toast-in pointer-events-auto flex items-center gap-2.5
              rounded-(--radius-md) bg-inverse-surface px-4 py-3 text-sm
              text-inverse-on-surface shadow-(--shadow-e3)"
          >
            <span className={`h-2 w-2 shrink-0 rounded-full ${dotColor[t.kind]}`} aria-hidden />
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
