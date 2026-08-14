// Typed API client for the Aurea backend. Token-aware, browser-side.

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

const TOKEN_KEY = "aurea_token";
const USER_KEY = "aurea_user";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}
export function setSession(token: string, user: any) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}
export function getUser(): any | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(USER_KEY);
  return raw ? JSON.parse(raw) : null;
}
export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Human-readable fallbacks for when the API returns no `detail` of its own. */
const HTTP_FALLBACK: Record<number, string> = {
  400: "That request wasn't valid.",
  403: "You don't have permission to view this.",
  404: "This isn't available yet.",
  408: "The server took too long to respond.",
  409: "That conflicts with something that already exists.",
  422: "Some of the details weren't valid.",
  429: "Too many requests — wait a moment and try again.",
  500: "The server hit an unexpected error.",
  502: "The server is unreachable right now.",
  503: "The service is temporarily unavailable.",
  504: "The server took too long to respond.",
};

export async function api<T = any>(
  path: string,
  opts: { method?: string; body?: any; token?: string | null } = {}
): Promise<T> {
  const token = opts.token ?? getToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: opts.method || (opts.body ? "POST" : "GET"),
      headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      cache: "no-store",
    });
  } catch {
    // Network-level failure (offline, DNS, CORS, server unreachable). `fetch` rejects
    // with an opaque "Failed to fetch" that means nothing to a user.
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  if (res.status === 401 && typeof window !== "undefined") {
    clearSession();
    if (!window.location.pathname.startsWith("/login")) window.location.href = "/login";
  }
  if (!res.ok) {
    // Prefer the API's own `detail`. Only fall back to a human sentence — never to
    // `res.statusText`, which surfaces raw reason phrases like "Not Found" as UI copy.
    let detail: unknown = null;
    try {
      const j = await res.json();
      detail = j?.detail ?? null;
    } catch {}
    const message =
      typeof detail === "string" && detail.trim()
        ? detail
        : detail
        ? JSON.stringify(detail)
        : HTTP_FALLBACK[res.status] || `Something went wrong (error ${res.status}).`;
    throw new ApiError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export type LoginResult = {
  access_token?: string;
  user?: any;
  mfa_required?: boolean;
  mfa_token?: string;
  mfa_setup_required?: boolean;
};

export async function login(email: string, password: string): Promise<LoginResult> {
  const data = await api<LoginResult>("/api/auth/login-json", { body: { email, password } });
  if (data.access_token && data.user) {
    setSession(data.access_token, data.user);
  }
  return data;
}
