import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { ProductOut } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Button, Spinner } from "../design";
import { ProductDrawer } from "../pages/ProductDrawer";
import type { Hit } from "./decode";
import { useScanner } from "./useScanner";

/** What the sheet is doing right now. The camera keeps running through
 *  lookups and misses (pausing only the decode loop keeps the next scan
 *  instant), but STOPS the moment an item is found — the drawer opens over
 *  the viewfinder, and a live camera behind a result you're reading is
 *  battery and privacy spent on nothing (Noah, 2026-09-01). "Scan again"
 *  restarts it, at the usual ~1s stream cost. */
type Phase =
  | { kind: "scanning" }
  | { kind: "looking"; code: string }
  | { kind: "missed"; code: string; reason: string }
  | { kind: "found"; code: string; product: ProductOut };

function ViewfinderFrame() {
  // corner brackets + a travelling line: the whole instruction set for "point
  // this at a barcode", with no words to translate
  const corner = "absolute h-7 w-7 border-white/85";
  return (
    <div className="pointer-events-none absolute inset-0 grid place-items-center">
      <div className="relative h-[42%] w-[86%]">
        <div className={`${corner} top-0 left-0 rounded-tl-lg border-t-3 border-l-3`} />
        <div className={`${corner} top-0 right-0 rounded-tr-lg border-t-3 border-r-3`} />
        <div className={`${corner} bottom-0 left-0 rounded-bl-lg border-b-3 border-l-3`} />
        <div className={`${corner} bottom-0 right-0 rounded-br-lg border-b-3 border-r-3`} />
        <div className="animate-scan-line absolute inset-x-3 top-1/2 h-0.5 rounded-full bg-primary shadow-[0_0_12px_var(--color-primary)]" />
      </div>
    </div>
  );
}

export function ScanSheet({ onClose }: { onClose: () => void }) {
  const { roles } = useAuth();
  const isAdmin = roles.has("admin");
  const navigate = useNavigate();
  const [phase, setPhase] = useState<Phase>({ kind: "scanning" });
  const [typed, setTyped] = useState("");
  const inFlight = useRef<string | null>(null);

  const lookUp = useCallback(async (code: string) => {
    const clean = code.trim();
    if (!clean || inFlight.current === clean) return;
    inFlight.current = clean;
    setPhase({ kind: "looking", code: clean });
    try {
      const product = await api<ProductOut>(`/products/by-barcode/${encodeURIComponent(clean)}`);
      setPhase({ kind: "found", code: clean, product });
    } catch (e) {
      // a 404 is a real answer ("we don't stock this"); anything else is the
      // lookup failing, and saying so beats implying the item doesn't exist
      const reason =
        e instanceof ApiError && e.status === 404
          ? "It may be a supplier code rather than the item's own barcode."
          : e instanceof Error
            ? `Lookup failed — ${e.message}`
            : "Lookup failed.";
      setPhase({ kind: "missed", code: clean, reason });
    } finally {
      inFlight.current = null;
    }
  }, []);

  const onHit = useCallback((hit: Hit) => void lookUp(hit.value), [lookUp]);

  // the loop idles while a lookup is in flight or a miss is on screen; the
  // camera itself powers OFF once an item is found (see the Phase comment)
  const scanning = phase.kind === "scanning";
  const scanner = useScanner({ enabled: phase.kind !== "found", paused: !scanning, onHit });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  const again = () => {
    setTyped("");
    setPhase({ kind: "scanning" });
  };

  const sheet = (
    <div className="animate-fade-in fixed inset-0 z-40 flex flex-col bg-black text-white">
      <div className="flex items-center justify-between px-4 pt-[max(0.75rem,env(safe-area-inset-top))] pb-3">
        <div className="text-[15px] font-semibold">Scan a barcode</div>
        <div className="flex items-center gap-1">
          {scanner.torchSupported && (
            <button
              onClick={scanner.toggleTorch}
              aria-label={scanner.torchOn ? "Turn off the light" : "Turn on the light"}
              aria-pressed={scanner.torchOn}
              className={`grid h-10 w-10 place-items-center rounded-full ${
                scanner.torchOn ? "bg-white text-black" : "bg-white/15 text-white"
              }`}
            >
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                <path
                  d="M9 1.5v3M9 13v3.5M3.5 9h-2M16.5 9h-2M5 5 3.6 3.6M13 5l1.4-1.4M5 13l-1.4 1.4M13 13l1.4 1.4"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                />
                <circle cx="9" cy="9" r="2.6" stroke="currentColor" strokeWidth="1.6" />
              </svg>
            </button>
          )}
          <button
            onClick={onClose}
            aria-label="Close scanner"
            className="grid h-10 w-10 place-items-center rounded-full bg-white/15"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      </div>

      <div className="relative min-h-0 flex-1 overflow-hidden">
        <video
          ref={scanner.videoRef}
          className="h-full w-full object-cover"
          playsInline
          muted
          autoPlay
        />
        {scanner.status === "scanning" && scanning && <ViewfinderFrame />}

        {scanner.status === "starting" && (
          <div className="absolute inset-0 grid place-items-center gap-3 bg-black/70 text-center">
            <Spinner />
          </div>
        )}

        {(scanner.status === "denied" ||
          scanner.status === "unavailable" ||
          scanner.status === "error") && (
          <div className="absolute inset-0 grid place-items-center bg-black/85 px-8 text-center">
            <p className="max-w-sm text-sm leading-6 text-white/85">
              {scanner.message || "The camera isn't available."}
              <br />
              <span className="text-white/60">Type or scan a code below instead.</span>
            </p>
          </div>
        )}

        {phase.kind === "looking" && (
          <div className="absolute inset-x-0 bottom-0 flex items-center gap-3 bg-black/75 px-5 py-4">
            <Spinner />
            <span className="font-mono text-sm">{phase.code}</span>
          </div>
        )}

        {phase.kind === "missed" && (
          <div className="absolute inset-x-0 bottom-0 bg-black/85 px-5 py-5">
            <p className="text-sm">
              Nothing in the catalog matches{" "}
              <span className="font-mono font-semibold">{phase.code}</span>.
            </p>
            <p className="mt-1 text-[13px] text-white/60">{phase.reason}</p>
            <div className="mt-4 flex gap-2">
              <Button onClick={again}>Scan again</Button>
              <Button
                variant="outlined"
                onClick={() => {
                  onClose();
                  navigate(`/catalog?search=${encodeURIComponent(phase.code)}`);
                }}
              >
                Search the catalog
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* Manual entry: a USB/bluetooth scanner types into this and hits Enter,
          and it's the way in when the camera is refused or missing. */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void lookUp(typed);
        }}
        className="flex items-center gap-2 px-4 pt-3 pb-[max(1rem,env(safe-area-inset-bottom))]"
      >
        {/* NOT inputMode="numeric": plenty of these codes have letters in
            them (CM233-L, US-SN0001), and a number pad can't type them. */}
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder="or type a barcode or SKU"
          inputMode="text"
          autoCapitalize="characters"
          autoCorrect="off"
          spellCheck={false}
          autoComplete="off"
          aria-label="Barcode or SKU"
          className="m3-control min-w-0 flex-1 rounded-full bg-white/12 px-4 py-3 text-white
            placeholder:text-white/45 focus:outline-2 focus:outline-primary"
        />
        <Button type="submit" disabled={!typed.trim()}>
          Look up
        </Button>
      </form>
    </div>
  );

  return (
    <>
      {createPortal(sheet, document.body)}
      {phase.kind === "found" && (
        <ProductDrawer product={phase.product} onClose={again} isAdmin={isAdmin} />
      )}
    </>
  );
}
