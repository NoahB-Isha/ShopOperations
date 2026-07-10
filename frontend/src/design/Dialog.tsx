import { useEffect } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

function Backdrop({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);
  return createPortal(
    <div
      className="animate-fade-in fixed inset-0 z-40 bg-scrim/35"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      {children}
    </div>,
    document.body,
  );
}

function CloseButton({ onClose }: { onClose: () => void }) {
  return (
    <button
      onClick={onClose}
      aria-label="Close"
      className="state-layer grid h-9 w-9 shrink-0 place-items-center rounded-full
        text-on-surface-variant"
    >
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    </button>
  );
}

/** M3 basic dialog: 28dp corners, surface-container-high, emphasized entrance. */
export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  wide = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  if (!open) return null;
  return (
    <Backdrop onClose={onClose}>
      <div className="flex min-h-full items-start justify-center p-4 pt-[8vh]">
        <div
          role="dialog"
          aria-label={title}
          className={`w-full ${wide ? "max-w-2xl" : "max-w-md"} animate-dialog-in
            rounded-(--radius-xl) bg-surface-container-high shadow-(--shadow-e3)`}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between gap-3 px-6 pt-5 pb-1">
            <h2 className="headline">{title}</h2>
            <CloseButton onClose={onClose} />
          </div>
          <div className="max-h-[65vh] overflow-y-auto px-6 py-4">{children}</div>
          {footer && <div className="flex justify-end gap-2 px-6 pt-1 pb-5">{footer}</div>}
        </div>
      </div>
    </Backdrop>
  );
}

/** M3 side sheet, sliding in with emphasized easing. */
export function Drawer({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  if (!open) return null;
  return (
    <Backdrop onClose={onClose}>
      <div
        role="dialog"
        className="animate-sheet-in fixed inset-y-0 right-0 flex w-full max-w-md flex-col
          rounded-l-(--radius-xl) bg-surface-container-low shadow-(--shadow-e3)"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 px-6 py-5">
          <h2 className="headline min-w-0 truncate pr-2">{title}</h2>
          <CloseButton onClose={onClose} />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-4">{children}</div>
        {footer && (
          <div className="flex justify-end gap-2 border-t border-outline-variant/60 px-6 py-4">
            {footer}
          </div>
        )}
      </div>
    </Backdrop>
  );
}
