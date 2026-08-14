import { lazy, Suspense, useState } from "react";
import { Icons } from "../nav";

// the camera sheet pulls in the decoder (and, on iOS, a wasm binary) — none of
// that should ride along in the shell's bundle for every page load
const ScanSheet = lazy(() => import("./ScanSheet").then((m) => ({ default: m.ScanSheet })));

/** Top-bar barcode scanner. Shown wherever the app bar is: phones use the
 *  camera, desks tend to have a wedge scanner that types into the same sheet. */
export function ScanButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        aria-label="Scan a barcode"
        title="Scan a barcode"
        className="state-layer grid h-10 w-10 place-items-center rounded-full text-on-surface-variant"
      >
        {Icons.scan}
      </button>
      {open && (
        <Suspense fallback={null}>
          <ScanSheet onClose={() => setOpen(false)} />
        </Suspense>
      )}
    </>
  );
}
