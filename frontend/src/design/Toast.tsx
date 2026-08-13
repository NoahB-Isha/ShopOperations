import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

type ToastKind = "success" | "error" | "info";

/** M3 snackbars carry at most one action — the undo for something that just
 *  happened without asking. */
export interface ToastAction {
  label: string;
  onClick: () => void;
}

interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
  action?: ToastAction;
}

type Push = (kind: ToastKind, message: string, action?: ToastAction) => void;

const ToastContext = createContext<Push>(() => {});

export function useToast() {
  const push = useContext(ToastContext);
  return useMemo(
    () => ({
      success: (m: string, action?: ToastAction) => push("success", m, action),
      error: (m: string, action?: ToastAction) => push("error", m, action),
      info: (m: string, action?: ToastAction) => push("info", m, action),
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

  const push = useCallback<Push>((kind, message, action) => {
    const id = nextId.current++;
    setToasts((t) => [...t, { id, kind, message, action }]);
    window.setTimeout(
      () => setToasts((t) => t.filter((x) => x.id !== id)),
      // an offer to undo needs long enough to notice and reach
      kind === "error" ? 6500 : action ? 7000 : 3800,
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
            <span className="min-w-0 flex-1">{t.message}</span>
            {t.action && (
              <button
                onClick={() => {
                  t.action?.onClick();
                  setToasts((all) => all.filter((x) => x.id !== t.id));
                }}
                className="state-layer -my-1 shrink-0 rounded-full px-2.5 py-1 text-[13px]
                  font-semibold text-inverse-primary"
              >
                {t.action.label}
              </button>
            )}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
