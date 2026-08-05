import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { AuthConfig, SessionOut } from "../api/types";
import { Button, Card, Field, Input, useToast } from "../design";
import { homeForRoles } from "../nav";
import { ILMark } from "../shell/AppShell";
import { useAuth } from "./AuthContext";

type Step = "identifier" | "code";

/** Provider ids the sign-in page knows how to render a button for. */
const PROVIDER_LABELS: Record<string, string> = { google: "Google" };

/** Google's brand mark, per their sign-in branding guidance. */
function GoogleMark() {
  return (
    <svg width="17" height="17" viewBox="0 0 18 18" aria-hidden focusable="false">
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.91c1.7-1.57 2.69-3.88 2.69-6.62Z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.91-2.26c-.81.54-1.84.86-3.05.86a5.36 5.36 0 0 1-5.03-3.71H1.05v2.34A9 9 0 0 0 9 18Z"
      />
      <path
        fill="#FBBC05"
        d="M3.97 10.71a5.4 5.4 0 0 1 0-3.42V4.96H1.05a9 9 0 0 0 0 8.09l2.92-2.34Z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.5.46 3.44 1.35l2.58-2.58A9 9 0 0 0 1.05 4.96l2.92 2.33A5.36 5.36 0 0 1 9 3.58Z"
      />
    </svg>
  );
}

async function supabaseClient(config: AuthConfig) {
  const { createClient } = await import("@supabase/supabase-js");
  return createClient(config.supabase_url, config.supabase_anon_key);
}

/** Strip the OAuth response from the address bar so a reload can't replay it. */
function clearOAuthParams() {
  window.history.replaceState({}, "", window.location.pathname);
}

export function LoginPage() {
  const { user, signIn } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();

  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [step, setStep] = useState<Step>("identifier");
  const [identifier, setIdentifier] = useState("");
  const [code, setCode] = useState("");
  const [devCode, setDevCode] = useState<string | null>(null);
  const [channel, setChannel] = useState("email");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const codeRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api<AuthConfig>("/auth/config").then(setConfig).catch(() => setConfig(null));
  }, []);

  // Coming back from the provider. supabase-js parses the URL during client
  // init (detectSessionInUrl), and getSession() awaits that — so this covers
  // both the PKCE (?code=) and implicit (#access_token) shapes.
  useEffect(() => {
    if (!config || config.mode !== "supabase") return;
    const params = new URLSearchParams(window.location.search);
    const providerError = params.get("error_description") ?? params.get("error");
    if (providerError) {
      setError(providerError);
      clearOAuthParams();
      return;
    }
    if (!params.has("code") && !window.location.hash.includes("access_token")) return;

    let cancelled = false;
    setBusy(true);
    void (async () => {
      try {
        const supabase = await supabaseClient(config);
        const { data, error: err } = await supabase.auth.getSession();
        if (err) throw new Error(err.message);
        if (!data.session) throw new Error("That sign-in didn't complete. Try again.");
        const session = await api<SessionOut>("/auth/exchange", {
          method: "POST",
          body: { supabase_token: data.session.access_token },
        });
        if (cancelled) return;
        signIn(session.token, session.user);
        toast.success(`Welcome back, ${session.user.display_name || "friend"}.`);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Sign-in failed.");
      } finally {
        clearOAuthParams();
        if (!cancelled) setBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [config, signIn, toast]);

  const signInWithProvider = async (provider: string) => {
    if (!config) return;
    setBusy(true);
    setError("");
    try {
      const supabase = await supabaseClient(config);
      // Redirects away; the effect above finishes the exchange on return.
      const { error: err } = await supabase.auth.signInWithOAuth({
        provider: provider as "google",
        options: { redirectTo: `${window.location.origin}/login` },
      });
      if (err) throw new Error(err.message);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't start that sign-in.");
      setBusy(false);
    }
  };

  useEffect(() => {
    if (user) navigate(homeForRoles(new Set(user.roles.map((r) => r.role))), { replace: true });
  }, [user, navigate]);

  useEffect(() => {
    if (step === "code") codeRef.current?.focus();
  }, [step]);

  const providers = config?.mode === "supabase" ? (config.oauth_providers ?? []) : [];
  // Missing config (fetch failed) keeps the code form, which is the old behavior.
  const otpEnabled = config?.otp_enabled ?? true;

  const requestCode = async () => {
    setBusy(true);
    setError("");
    try {
      if (config?.mode === "supabase") {
        const { createClient } = await import("@supabase/supabase-js");
        const supabase = createClient(config.supabase_url, config.supabase_anon_key);
        const isEmail = identifier.includes("@");
        const { error: err } = await supabase.auth.signInWithOtp(
          isEmail ? { email: identifier.trim() } : { phone: identifier.trim() },
        );
        if (err) throw new Error(err.message);
        setChannel(isEmail ? "email" : "sms");
      } else {
        const r = await api<{ sent: boolean; channel: string; dev_code: string | null }>(
          "/auth/request-code",
          { method: "POST", body: { identifier } },
        );
        setChannel(r.channel);
        setDevCode(r.dev_code);
        if (r.dev_code) setCode(r.dev_code);
      }
      setStep("code");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  const verify = async () => {
    setBusy(true);
    setError("");
    try {
      let session: SessionOut;
      if (config?.mode === "supabase") {
        const { createClient } = await import("@supabase/supabase-js");
        const supabase = createClient(config.supabase_url, config.supabase_anon_key);
        const isEmail = identifier.includes("@");
        const { data, error: err } = await supabase.auth.verifyOtp(
          isEmail
            ? { email: identifier.trim(), token: code.trim(), type: "email" }
            : { phone: identifier.trim(), token: code.trim(), type: "sms" },
        );
        if (err || !data.session) throw new Error(err?.message ?? "Verification failed.");
        session = await api<SessionOut>("/auth/exchange", {
          method: "POST",
          body: { supabase_token: data.session.access_token },
        });
      } else {
        session = await api<SessionOut>("/auth/verify", {
          method: "POST",
          body: { identifier, code },
        });
      }
      signIn(session.token, session.user);
      toast.success(`Welcome back, ${session.user.display_name || "friend"}.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That code didn't work.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative grid min-h-dvh place-items-center overflow-hidden bg-surface px-4">
      {/* tonal blobs — the fun lives in the background, and it drifts */}
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div className="animate-drift absolute -top-24 -left-24 h-80 w-80 rounded-full bg-primary-container/80 blur-3xl" />
        <div className="animate-drift absolute -right-20 -bottom-32 h-96 w-96 rounded-full bg-tertiary-container/70 blur-3xl [animation-delay:-5s]" />
        <div className="animate-drift absolute top-10 right-1/4 h-44 w-44 rounded-full bg-secondary-container/60 blur-2xl [animation-delay:-10s]" />
      </div>

      <div className="stagger-children relative w-full max-w-sm">
        <div className="mb-7 flex flex-col items-center gap-4 text-center">
          <ILMark size={120} />
          <div>
            <h1 className="display-l">Isha Life Shop Ops</h1>
            <p className="mt-2 text-[15px] text-on-surface-variant">
              Inventory, transfers & ordering for North America
            </p>
          </div>
        </div>

        <Card variant="elevated" pad={false} className="rounded-(--radius-xl) p-7">
          {providers.length > 0 && (
            <div className="flex flex-col gap-3">
              {providers.map((p) => (
                <Button
                  key={p}
                  type="button"
                  variant="outlined"
                  loading={busy}
                  onClick={() => void signInWithProvider(p)}
                  icon={p === "google" ? <GoogleMark /> : undefined}
                >
                  Continue with {PROVIDER_LABELS[p] ?? p}
                </Button>
              ))}
              {error && !otpEnabled && (
                <p role="alert" className="text-center text-[13px] text-error">
                  {error}
                </p>
              )}
            </div>
          )}

          {providers.length > 0 && otpEnabled && (
            <div className="my-5 flex items-center gap-3" aria-hidden>
              <span className="h-px flex-1 bg-outline-variant" />
              <span className="text-[12px] font-medium text-on-surface-variant">or</span>
              <span className="h-px flex-1 bg-outline-variant" />
            </div>
          )}

          {providers.length === 0 && !otpEnabled && (
            <p className="text-center text-sm text-on-surface-variant">
              No sign-in method is configured on this server. Ask the office to enable one.
            </p>
          )}

          {!otpEnabled ? null : step === "identifier" ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void requestCode();
              }}
              className="flex flex-col gap-4"
            >
              <Field
                label="Email or phone"
                help="We'll send a fresh one-time code — no passwords here."
                error={error}
              >
                <Input
                  autoFocus
                  placeholder="you@example.org or (555) 123-4567"
                  value={identifier}
                  onChange={(e) => setIdentifier(e.target.value)}
                  autoComplete="username"
                  inputMode="email"
                />
              </Field>
              <Button type="submit" loading={busy} disabled={!identifier.trim()}>
                Send code
              </Button>
            </form>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void verify();
              }}
              className="flex flex-col gap-4"
            >
              <div className="text-sm text-on-surface-variant">
                Code sent {channel === "email" ? "to" : "by text to"}{" "}
                <span className="font-medium text-on-surface">{identifier}</span>
              </div>
              {devCode && (
                <div
                  className="animate-pop-in rounded-(--radius-md) bg-tertiary-container px-3.5
                    py-2.5 text-[13px] text-on-tertiary-container"
                  data-testid="dev-code"
                >
                  Dev mode — your code is <span className="font-mono font-bold">{devCode}</span>{" "}
                  (pre-filled below)
                </div>
              )}
              <Field label="One-time code" error={error}>
                <Input
                  ref={codeRef}
                  value={code}
                  // 10, not 6: Supabase's email OTP length is configurable
                  // (6-10 digits) — a shorter mask would silently truncate
                  // the code and every login would fail as "invalid"
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 10))}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  className="text-center font-mono text-xl tracking-[0.5em]"
                />
              </Field>
              <Button type="submit" loading={busy} disabled={code.length < 6}>
                Sign in
              </Button>
              <button
                type="button"
                onClick={() => {
                  setStep("identifier");
                  setCode("");
                  setDevCode(null);
                  setError("");
                }}
                className="text-center text-[13px] font-medium text-primary underline-offset-2 hover:underline"
              >
                Use a different email or phone
              </button>
            </form>
          )}
        </Card>

        <p className="mt-5 text-center text-[12.5px] leading-5 text-on-surface-variant">
          Sessions last 30 days on trusted devices.
          <br />
          Need access? Ask the office to send you an invite.
        </p>
      </div>
    </div>
  );
}
