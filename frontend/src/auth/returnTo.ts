/* Where someone was headed before we sent them to sign in.

   sessionStorage rather than router state, because signing in with Google is
   a full page navigation to the provider and back — router state doesn't
   survive leaving the document, so a QR scan that landed on /order?center=7
   would come back to the role's home page instead.

   Only same-origin PATHS are ever stored: this value decides where the app
   sends a freshly-signed-in person, so anything that could name another host
   ("//evil.example", "https://…", a scheme) is refused rather than sanitized. */
const KEY = "ilops_return_to";

/** A path we're willing to send someone to after sign-in, or null. */
export function safeReturnPath(value: string | null | undefined): string | null {
  const v = (value ?? "").trim();
  // one leading slash, and no second one — "//host" is protocol-relative
  if (!v.startsWith("/") || v.startsWith("//")) return null;
  if (v.startsWith("/\\")) return null; // browsers treat \ as / in this position
  if (v === "/login" || v.startsWith("/login?")) return null; // no loops
  return v;
}

export function rememberReturnTo(path: string): void {
  const safe = safeReturnPath(path);
  try {
    if (safe) sessionStorage.setItem(KEY, safe);
  } catch {
    /* private mode — falling back to the role's home page is fine */
  }
}

/** Read once and forget: a stale destination must not hijack a later sign-in. */
export function takeReturnTo(): string | null {
  try {
    const stored = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY);
    return safeReturnPath(stored);
  } catch {
    return null;
  }
}
