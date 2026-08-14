import { useCallback, useEffect, useRef, useState } from "react";
import type { Decoder, Hit } from "./decode";
import { makeDecoder } from "./decode";

export type ScanStatus = "off" | "starting" | "scanning" | "denied" | "unavailable" | "error";

/** Fraction of the frame the viewfinder covers. Cropping to it is what keeps
 *  the wasm path quick: a 1280×720 frame is ~900k pixels, this band is ~180k,
 *  and a barcode outside the window wasn't the one being aimed at anyway. */
const ROI = { w: 0.86, h: 0.42 };
/** Longest edge handed to the decoder. Beyond this, resolution buys nothing
 *  for a 1D symbol and costs real milliseconds per frame. */
const ROI_MAX_PX = 720;

/** `requestVideoFrameCallback` fires once per decoded video frame — better
 *  than rAF here, because it never hands the decoder the same picture twice.
 *  TypeScript's DOM lib types it as always present; Firefox disagrees, so the
 *  call sites check at runtime. */
function hasFrameCallback(v: HTMLVideoElement | null): boolean {
  return typeof v?.requestVideoFrameCallback === "function";
}

export interface Scanner {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  status: ScanStatus;
  /** Honest, human-facing reason when status isn't `scanning`. */
  message: string;
  engine: "native" | "wasm" | null;
  torchOn: boolean;
  torchSupported: boolean;
  toggleTorch: () => void;
}

/**
 * Live camera scanning.
 *
 * `enabled` owns the camera stream (starting one costs ~1s and lights the
 * privacy indicator, so it follows the sheet being open). `paused` only stops
 * the decode loop — that's what a result freeze uses, so "scan again" is
 * instant instead of a second camera warm-up.
 */
export function useScanner({
  enabled,
  paused,
  onHit,
}: {
  enabled: boolean;
  paused: boolean;
  onHit: (hit: Hit) => void;
}): Scanner {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const decoderRef = useRef<Decoder | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const pausedRef = useRef(paused);
  const runningRef = useRef(false);
  const pendingRef = useRef<string | null>(null);
  const onHitRef = useRef(onHit);

  const [status, setStatus] = useState<ScanStatus>("off");
  const [message, setMessage] = useState("");
  const [engine, setEngine] = useState<"native" | "wasm" | null>(null);
  const [torchOn, setTorchOn] = useState(false);
  const [torchSupported, setTorchSupported] = useState(false);

  onHitRef.current = onHit;
  pausedRef.current = paused;

  /** Crop the viewfinder band out of the current frame for the wasm decoder. */
  const readRoi = useCallback((): ImageData | null => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return null;
    const sw = Math.round(video.videoWidth * ROI.w);
    const sh = Math.round(video.videoHeight * ROI.h);
    const scale = Math.min(1, ROI_MAX_PX / Math.max(sw, sh));
    const dw = Math.max(1, Math.round(sw * scale));
    const dh = Math.max(1, Math.round(sh * scale));
    const canvas = (canvasRef.current ??= document.createElement("canvas"));
    if (canvas.width !== dw || canvas.height !== dh) {
      canvas.width = dw;
      canvas.height = dh;
    }
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) return null;
    ctx.drawImage(
      video,
      Math.round((video.videoWidth - sw) / 2),
      Math.round((video.videoHeight - sh) / 2),
      sw,
      sh,
      0,
      0,
      dw,
      dh,
    );
    return ctx.getImageData(0, 0, dw, dh);
  }, []);

  const toggleTorch = useCallback(() => {
    const track = streamRef.current?.getVideoTracks()[0];
    if (!track) return;
    const next = !torchOn;
    void track
      .applyConstraints({ advanced: [{ torch: next }] } as unknown as MediaTrackConstraints)
      .then(() => setTorchOn(next))
      .catch(() => setTorchSupported(false));
  }, [torchOn]);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let frameHandle: number | null = null;
    let timer: number | null = null;

    const stopLoop = () => {
      const video = videoRef.current;
      if (frameHandle !== null && hasFrameCallback(video)) {
        video?.cancelVideoFrameCallback(frameHandle);
      }
      if (timer !== null) window.clearTimeout(timer);
      frameHandle = null;
      timer = null;
    };

    const schedule = (fn: () => void) => {
      const video = videoRef.current;
      if (video && hasFrameCallback(video)) {
        frameHandle = video.requestVideoFrameCallback(fn);
      } else {
        // ~60fps ceiling; the decode itself is what actually paces this loop
        timer = window.setTimeout(fn, 16);
      }
    };

    const tick = () => {
      if (cancelled) return;
      const decoder = decoderRef.current;
      const video = videoRef.current;
      if (!decoder || !video || pausedRef.current || runningRef.current || video.readyState < 2) {
        schedule(tick);
        return;
      }
      runningRef.current = true;
      void decoder
        .decode(video, readRoi)
        .then((hit) => {
          if (cancelled || !hit || pausedRef.current) return;
          if (!hit.trusted && pendingRef.current !== hit.value) {
            // no check digit on this symbology — insist on seeing it twice
            pendingRef.current = hit.value;
            return;
          }
          pendingRef.current = null;
          navigator.vibrate?.(30);
          onHitRef.current(hit);
        })
        .catch(() => {
          /* a bad frame is not an error worth surfacing — keep scanning */
        })
        .finally(() => {
          runningRef.current = false;
          if (!cancelled) schedule(tick);
        });
    };

    const start = async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        setStatus("unavailable");
        setMessage(
          window.isSecureContext
            ? "This browser can't open a camera."
            : "Camera access needs a secure (https) connection.",
        );
        return;
      }
      setStatus("starting");
      setMessage("");
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: { ideal: "environment" },
            width: { ideal: 1280 },
            height: { ideal: 720 },
          },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        const track = stream.getVideoTracks()[0];
        // continuous autofocus is the difference between "reads instantly" and
        // "hold still and pray" on a close-up label
        void track
          ?.applyConstraints({
            advanced: [{ focusMode: "continuous" }],
          } as unknown as MediaTrackConstraints)
          .catch(() => {});
        const caps = track?.getCapabilities?.() as { torch?: boolean } | undefined;
        setTorchSupported(Boolean(caps?.torch));

        const video = videoRef.current;
        if (video) {
          video.srcObject = stream;
          await video.play().catch(() => {});
        }
        const decoder = await makeDecoder();
        if (cancelled) return;
        decoderRef.current = decoder;
        setEngine(decoder.engine);
        setStatus("scanning");
        schedule(tick);
      } catch (e) {
        if (cancelled) return;
        const name = e instanceof DOMException ? e.name : "";
        if (name === "NotAllowedError" || name === "SecurityError") {
          setStatus("denied");
          setMessage("Camera permission was declined. Allow it in your browser settings, or type the barcode below.");
        } else if (name === "NotFoundError" || name === "OverconstrainedError") {
          setStatus("unavailable");
          setMessage("No camera found on this device.");
        } else {
          setStatus("error");
          setMessage(e instanceof Error ? e.message : "The camera didn't start.");
        }
      }
    };

    void start();

    return () => {
      cancelled = true;
      stopLoop();
      decoderRef.current = null;
      pendingRef.current = null;
      runningRef.current = false;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      setTorchOn(false);
      setStatus("off");
    };
  }, [enabled, readRoi]);

  return { videoRef, status, message, engine, torchOn, torchSupported, toggleTorch };
}
