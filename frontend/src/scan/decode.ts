/** Barcode decoding, two engines behind one call.
 *
 *  1. `BarcodeDetector` — the platform's own decoder (Chrome/Android, Edge).
 *     It reads the <video> element directly, so there is no pixel copy at all
 *     and a hit costs well under a frame.
 *  2. zxing-wasm — the fallback, and the ONLY engine on iOS Safari, which has
 *     never shipped BarcodeDetector. The phones the floor team carry are the
 *     whole point of this feature, so "native only" would have shipped a dead
 *     button to half of them.
 *
 *  The wasm module and its ~1MB binary are dynamically imported, so the native
 *  path never downloads them.
 */

import type { ReadInputBarcodeFormat } from "zxing-wasm/reader";

/** Retail + shelf-label symbologies, in the two engines' spellings. Limiting
 *  the set is the single biggest speed lever: every extra symbology is another
 *  pass over the same pixels. */
const NATIVE_FORMATS = [
  "ean_13",
  "ean_8",
  "upc_a",
  "upc_e",
  "code_128",
  "code_39",
  "itf",
];
const ZXING_FORMATS: ReadInputBarcodeFormat[] = [
  "EAN-13",
  "EAN-8",
  "UPC-A",
  "UPC-E",
  "Code128",
  "Code39",
  "ITF",
];

/** Formats that carry a check digit the decoder verifies. A single clean read
 *  of one of these is trustworthy; the others (Code 39/128/ITF as used on
 *  shelf labels) can misread a smudge, so those wait for a second identical
 *  read. Accuracy where it's cheap, speed where it's safe. */
const SELF_CHECKING = new Set([
  "ean_13",
  "ean_8",
  "upc_a",
  "upc_e",
  "EAN-13",
  "EAN-8",
  "UPC-A",
  "UPC-E",
]);

export interface Hit {
  value: string;
  format: string;
  /** True when the format's own check digit already vouched for the read. */
  trusted: boolean;
}

interface NativeDetector {
  detect(source: CanvasImageSource): Promise<{ rawValue: string; format: string }[]>;
}
type DetectorCtor = new (opts: { formats: string[] }) => NativeDetector;

function nativeCtor(): DetectorCtor | null {
  const ctor = (window as unknown as { BarcodeDetector?: DetectorCtor }).BarcodeDetector;
  return typeof ctor === "function" ? ctor : null;
}

export type Engine = "native" | "wasm";

export interface Decoder {
  engine: Engine;
  /** Decode one frame. `roi` is the already-cropped region for the wasm path;
   *  the native path reads the video element directly. */
  decode(video: HTMLVideoElement, roi: () => ImageData | null): Promise<Hit | null>;
}

async function nativeDecoder(ctor: DetectorCtor): Promise<Decoder> {
  const detector = new ctor({ formats: NATIVE_FORMATS });
  return {
    engine: "native",
    async decode(video) {
      const found = await detector.detect(video);
      const first = found.find((f) => f.rawValue);
      if (!first) return null;
      return {
        value: first.rawValue,
        format: first.format,
        trusted: SELF_CHECKING.has(first.format),
      };
    },
  };
}

async function wasmDecoder(): Promise<Decoder> {
  const [{ prepareZXingModule, readBarcodes }, wasmUrl] = await Promise.all([
    import("zxing-wasm/reader"),
    // the binary is served from our own origin, never a CDN — the deployed CSP
    // allows no other host, and an offline stockroom shouldn't need one
    import("zxing-wasm/reader/zxing_reader.wasm?url").then((m) => m.default),
  ]);
  await prepareZXingModule({
    overrides: { locateFile: (path: string, prefix: string) =>
      path.endsWith(".wasm") ? wasmUrl : prefix + path },
    fireImmediately: true,
  });
  return {
    engine: "wasm",
    async decode(_video, roi) {
      const image = roi();
      if (!image) return null;
      const found = await readBarcodes(image, {
        formats: ZXING_FORMATS,
        // speed over exhaustiveness: the frames keep coming, so a miss on this
        // one costs ~30ms, while tryHarder/tryRotate cost that on EVERY frame
        tryHarder: false,
        tryRotate: false,
        tryInvert: false,
        maxNumberOfSymbols: 1,
      });
      const first = found.find((f) => f.isValid && f.text);
      if (!first) return null;
      return {
        value: first.text,
        format: String(first.format),
        trusted: SELF_CHECKING.has(String(first.format)),
      };
    },
  };
}

/** Build the best decoder this browser can offer. */
export async function makeDecoder(): Promise<Decoder> {
  const ctor = nativeCtor();
  if (ctor) {
    try {
      return await nativeDecoder(ctor);
    } catch {
      // Some Androids expose the constructor but fail to build one for a
      // format list they don't actually support — fall through rather than
      // leaving the scanner dead.
    }
  }
  return wasmDecoder();
}
