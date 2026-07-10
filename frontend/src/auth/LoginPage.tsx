import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { AuthConfig, SessionOut } from "../api/types";
import { Button, Card, Field, Input, useToast } from "../design";
import { homeForRoles } from "../nav";
import { useAuth } from "./AuthContext";

type Step = "identifier" | "code";

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

  useEffect(() => {
    if (user) navigate(homeForRoles(new Set(user.roles.map((r) => r.role))), { replace: true });
  }, [user, navigate]);

  useEffect(() => {
    if (step === "code") codeRef.current?.focus();
  }, [step]);

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
    <div className="grid min-h-dvh place-items-center bg-canvas px-4">
      <div className="w-full max-w-sm">
        <div className="mb-7 flex flex-col items-center gap-3 text-center">
          <span className="grid h-12 w-12 place-items-center rounded-full bg-copper shadow-soft">
            <span className="h-4 w-4 rounded-full bg-surface" />
          </span>
          <div>
            <h1 className="display text-2xl">Isha Life Shop Ops</h1>
            <p className="mt-1 text-sm text-ink-faint">
              Inventory, transfers & ordering for North America
            </p>
          </div>
        </div>

        <Card className="p-6">
          {step === "identifier" ? (
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
              <div className="text-sm text-ink-soft">
                Code sent {channel === "email" ? "to" : "by text to"}{" "}
                <span className="font-medium text-ink">{identifier}</span>
              </div>
              {devCode && (
                <div className="rounded-(--radius-sm) border border-gold/40 bg-gold-tint px-3 py-2
                  text-[13px] text-gold" data-testid="dev-code">
                  Dev mode — your code is <span className="font-mono font-bold">{devCode}</span>{" "}
                  (pre-filled below)
                </div>
              )}
              <Field label="One-time code" error={error}>
                <Input
                  ref={codeRef}
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  placeholder="••••••"
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
                className="text-center text-[13px] text-ink-faint hover:text-copper-deep"
              >
                Use a different email or phone
              </button>
            </form>
          )}
        </Card>

        <p className="mt-5 text-center text-[12.5px] leading-5 text-ink-faint">
          Sessions last 30 days on trusted devices.
          <br />
          Need access? Ask the office to send you an invite.
        </p>
      </div>
    </div>
  );
}
