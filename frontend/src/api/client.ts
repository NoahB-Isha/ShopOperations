const TOKEN_KEY = "ilops_token";

/** API origin. Same-origin by default (the dev proxy / on-box deploys);
 *  set VITE_API_BASE when the frontend is hosted elsewhere (e.g. Vercel)
 *  and the backend lives on its own domain. */
const API_BASE: string = import.meta.env.VITE_API_BASE || window.location.origin;

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(
  path: string,
  options: { method?: string; body?: unknown; params?: Record<string, string | number | boolean | undefined> } = {},
): Promise<T> {
  const url = new URL(`/api/v1${path}`, API_BASE);
  for (const [k, v] of Object.entries(options.params ?? {})) {
    if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
  }
  const token = getToken();
  const resp = await fetch(url, {
    method: options.method ?? "GET",
    headers: {
      ...(options.body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });
  if (resp.status === 401 && !path.startsWith("/auth/")) {
    setToken(null);
    window.location.assign("/login");
    throw new ApiError(401, "Session expired — please sign in again.");
  }
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const data = await resp.json();
      if (typeof data.detail === "string") detail = data.detail;
      else if (data.detail) detail = JSON.stringify(data.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, detail);
  }
  // DELETEs answer 204 with no body — json() would throw on success
  if (resp.status === 204) return undefined as T;
  const text = await resp.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** Multipart request (file uploads) — same auth/error handling as api(). */
export async function apiUpload<T>(path: string, form: FormData, method = "POST"): Promise<T> {
  const token = getToken();
  const resp = await fetch(new URL(`/api/v1${path}`, API_BASE), {
    method,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (resp.status === 401) {
    setToken(null);
    window.location.assign("/login");
    throw new ApiError(401, "Session expired — please sign in again.");
  }
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const data = await resp.json();
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, detail);
  }
  const text = await resp.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** Authorized image fetch for display: same bearer-in-a-header rule as
 *  apiDownload, handed back as an object URL. The caller MUST revoke it when
 *  the element unmounts — an un-revoked blob holds the bytes for the life of
 *  the document. */
export async function apiBlobUrl(path: string): Promise<string> {
  const token = getToken();
  const resp = await fetch(new URL(`/api/v1${path}`, API_BASE), {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!resp.ok) throw new ApiError(resp.status, `couldn't load (${resp.status})`);
  return URL.createObjectURL(await resp.blob());
}

/** Authorized file download: fetch with the bearer token, save via a blob
 *  link (tokens never ride in URLs). */
export async function apiDownload(path: string, fallbackName: string): Promise<void> {
  const token = getToken();
  const resp = await fetch(new URL(`/api/v1${path}`, API_BASE), {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!resp.ok) throw new ApiError(resp.status, `download failed (${resp.status})`);
  const blob = await resp.blob();
  const disposition = resp.headers.get("content-disposition") ?? "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = match?.[1] ?? fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}
