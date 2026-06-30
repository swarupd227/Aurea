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

export async function api<T = any>(
  path: string,
  opts: { method?: string; body?: any; token?: string | null } = {}
): Promise<T> {
  const token = opts.token ?? getToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}${path}`, {
    method: opts.method || (opts.body ? "POST" : "GET"),
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    cache: "no-store",
  });
  if (res.status === 401 && typeof window !== "undefined") {
    clearSession();
    if (!window.location.pathname.startsWith("/login")) window.location.href = "/login";
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch {}
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
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
