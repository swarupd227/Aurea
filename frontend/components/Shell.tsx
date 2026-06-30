"use client";
import { ReactNode, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  LayoutDashboard, Users, ClipboardCheck, Bot, MessageSquareText, ShieldCheck,
  Sparkles, Settings, LogOut, PlugZap, UserPlus, GitMerge, CalendarClock, FileText, CheckSquare,
  MessageSquare, Gauge, BarChart3, Workflow, PiggyBank, Scale, Wand2, KeyRound, Eye, EyeOff, Bell,
  AlertTriangle, CheckCircle2, Building2, Shield, Copy, Check, History, UserCircle,
} from "lucide-react";
import { clearSession, getToken, getUser } from "@/lib/api";
import RoleSwitcher from "./RoleSwitcher";

// roles: which personas see an item. Omitted ⇒ all staff. 'admin' always sees everything.
type NavItem = { href: string; label: string; icon: any; roles?: string[] };
type NavGroup = { title: string; items: NavItem[] };

const A = "adviser", P = "paraplanner", PT = "portfolio_team", R = "research_cio",
  C = "compliance", O = "operations", B = "branch_leader";

const STAFF_NAV: NavGroup[] = [
  {
    title: "Studio",
    items: [
      { href: "/studio", label: "Cockpit", icon: LayoutDashboard, roles: [A, P, PT, B] },
      { href: "/studio/workforce", label: "Workforce", icon: Workflow, roles: [A, PT, C, B] },
      { href: "/studio/skills", label: "Skills", icon: Wand2, roles: [A, P, PT] },
      { href: "/studio/clients", label: "Clients", icon: Users, roles: [A, P, PT, R, O, B] },
      { href: "/studio/review", label: "Recommendations", icon: ClipboardCheck, roles: [A, PT, C] },
      { href: "/studio/ask", label: "Ask your book", icon: MessageSquareText, roles: [A, P, R, B] },
      { href: "/studio/analytics", label: "Analytics", icon: BarChart3, roles: [A, PT, R, B] },
      { href: "/studio/tasks", label: "Tasks", icon: CheckSquare, roles: [A, P] },
      { href: "/studio/capacity", label: "Capacity & outcomes", icon: Gauge, roles: [B, A] },
    ],
  },
  {
    title: "Acquire & onboard",
    items: [
      { href: "/studio/onboarding", label: "Onboarding", icon: UserPlus, roles: [O, C, A] },
      { href: "/studio/book-integration", label: "Book integration", icon: GitMerge, roles: [O] },
    ],
  },
  {
    title: "Advise & engage",
    items: [
      { href: "/studio/meetings", label: "Meetings", icon: CalendarClock, roles: [A, P] },
      { href: "/studio/reports", label: "Reports", icon: FileText, roles: [A, P, R] },
      { href: "/studio/messages", label: "Messages", icon: MessageSquare, roles: [A, P] },
      { href: "/studio/family", label: "Family aggregate", icon: UserCircle, roles: [A, P, PT] },
      { href: "/studio/agent-history", label: "Agent history", icon: History, roles: [A, PT, C, B] },
    ],
  },
  {
    title: "Governance",
    items: [{ href: "/provenance", label: "Provenance", icon: ShieldCheck, roles: [C, B] }],
  },
  {
    title: "Experience",
    items: [
      { href: "/canvas", label: "Client view — Canvas", icon: Sparkles, roles: [A, P, B] },
      { href: "/canvas/retirement", label: "Client view — Retirement", icon: PiggyBank, roles: [A, P, B] },
    ],
  },
  {
    title: "Configure",
    items: [
      { href: "/admin", label: "Firm & agents", icon: Settings, roles: [] },  // admin-only
      { href: "/admin/foundation", label: "Common foundation", icon: ShieldCheck, roles: [] },
      { href: "/admin/regulatory", label: "Regulatory", icon: Scale, roles: [C] },
      { href: "/admin/connectors", label: "Connectors", icon: PlugZap, roles: [O] },
    ],
  },
  {
    title: "Platform",
    items: [
      { href: "/superadmin", label: "Firm management", icon: Building2, roles: ["superadmin_only"] },
    ],
  },
];

function navForRole(role: string): NavGroup[] {
  if (role === "superadmin") {
    // Superadmin sees only their specific section
    return STAFF_NAV.filter((g) => g.title === "Platform").map((g) => ({
      ...g,
      items: g.items.filter((i) => i.roles?.includes("superadmin_only") ?? false),
    }));
  }
  if (role === "admin") {
    // Admin sees everything except superadmin-only items
    return STAFF_NAV.map((g) => ({
      ...g,
      items: g.items.filter((i) => !i.roles?.includes("superadmin_only")),
    })).filter((g) => g.items.length > 0);
  }
  return STAFF_NAV.map((g) => ({
    ...g,
    items: g.items.filter((i) => !i.roles || (!i.roles.includes("superadmin_only") && i.roles.includes(role))),
  })).filter((g) => g.items.length > 0);
}

export default function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<any>(null);
  const [showChangePwd, setShowChangePwd] = useState(false);
  const [pwdForm, setPwdForm] = useState({ current: "", next: "", confirm: "" });
  const [pwdErr, setPwdErr] = useState("");
  const [pwdOk, setPwdOk] = useState(false);
  const [pwdLoading, setPwdLoading] = useState(false);
  const [showPwd, setShowPwd] = useState(false);
  const [showNotifs, setShowNotifs] = useState(false);
  const [notifItems, setNotifItems] = useState<any[]>([]);
  const [notifCount, setNotifCount] = useState(0);

  // MFA modal state
  const [showMfa, setShowMfa] = useState(false);
  const [mfaStep, setMfaStep] = useState<"info" | "setup" | "confirm" | "disable">("info");
  const [mfaSecret, setMfaSecret] = useState("");
  const [mfaUri, setMfaUri] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [mfaMsg, setMfaMsg] = useState("");
  const [mfaBusy, setMfaBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    setUser(getUser());
  }, [router]);

  useEffect(() => {
    if (!user || user.role === "client") return;
    const poll = async () => {
      try {
        const { api } = await import("@/lib/api");
        const d = await api<{ count: number; items: any[] }>("/api/studio/notifications");
        setNotifCount(d.count);
        setNotifItems(d.items);
      } catch {}
    };
    poll();
    const id = setInterval(poll, 60_000);
    return () => clearInterval(id);
  }, [user]);

  if (!user) return null;
  const isClient = user.role === "client";
  const branding = user.firm?.branding || {};

  async function submitChangePwd(e: React.FormEvent) {
    e.preventDefault();
    setPwdErr("");
    if (pwdForm.next.length < 8) { setPwdErr("New password must be at least 8 characters."); return; }
    if (pwdForm.next !== pwdForm.confirm) { setPwdErr("Passwords do not match."); return; }
    setPwdLoading(true);
    try {
      const { api } = await import("@/lib/api");
      await api("/api/auth/change-password", { body: { current_password: pwdForm.current, new_password: pwdForm.next } });
      setPwdOk(true);
      setTimeout(() => { setShowChangePwd(false); setPwdForm({ current: "", next: "", confirm: "" }); setPwdOk(false); }, 1500);
    } catch (err: any) {
      setPwdErr(err.message || "Error changing password.");
    } finally {
      setPwdLoading(false);
    }
  }

  function logout() {
    clearSession();
    router.replace("/login");
  }

  async function openMfa() {
    setMfaMsg("");
    setMfaCode("");
    setMfaStep(user.mfa_enabled ? "info" : "info");
    setShowMfa(true);
  }

  async function startMfaSetup() {
    setMfaBusy(true);
    setMfaMsg("");
    try {
      const { api: _api } = await import("@/lib/api");
      const d = await _api<{ secret: string; otpauth_uri: string }>("/api/auth/mfa/setup", { body: {} });
      setMfaSecret(d.secret);
      setMfaUri(d.otpauth_uri);
      setMfaStep("setup");
    } catch (e: any) {
      setMfaMsg(e.message || "Error generating MFA secret.");
    } finally {
      setMfaBusy(false);
    }
  }

  async function confirmMfa() {
    setMfaBusy(true);
    setMfaMsg("");
    try {
      const { api: _api } = await import("@/lib/api");
      await _api("/api/auth/mfa/confirm", { body: { code: mfaCode.trim() } });
      // Update local user state
      const updated = { ...user, mfa_enabled: true };
      if (typeof window !== "undefined") {
        localStorage.setItem("aurea_user", JSON.stringify(updated));
      }
      setUser(updated);
      setMfaStep("info");
      setMfaMsg("MFA enabled successfully.");
      setMfaCode("");
    } catch (e: any) {
      setMfaMsg(e.message || "Invalid code.");
    } finally {
      setMfaBusy(false);
    }
  }

  async function disableMfa() {
    setMfaBusy(true);
    setMfaMsg("");
    try {
      const { api: _api } = await import("@/lib/api");
      await _api("/api/auth/mfa/disable", { body: { code: mfaCode.trim() } });
      const updated = { ...user, mfa_enabled: false };
      if (typeof window !== "undefined") {
        localStorage.setItem("aurea_user", JSON.stringify(updated));
      }
      setUser(updated);
      setMfaStep("info");
      setMfaMsg("MFA disabled.");
      setMfaCode("");
    } catch (e: any) {
      setMfaMsg(e.message || "Invalid code.");
    } finally {
      setMfaBusy(false);
    }
  }

  function copySecret() {
    navigator.clipboard.writeText(mfaSecret);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const nav = isClient
    ? [{ title: "Experience", items: [
        { href: "/canvas", label: "My wealth", icon: Sparkles },
        { href: "/canvas/retirement", label: "My retirement", icon: PiggyBank },
      ] }]
    : navForRole(user.role);

  return (
    <div className="min-h-screen flex bg-paper">
      {/* Sidebar */}
      <aside className="w-64 shrink-0 bg-navy-900 text-navy-100 flex flex-col fixed h-screen">
        <div className="px-5 py-5 flex items-center gap-2.5 border-b border-white/5">
          <Mark accent={branding.accent} />
          <div>
            <div className="text-white font-semibold leading-none">Aurera</div>
            <div className="text-[10px] uppercase tracking-wider text-navy-200/70 mt-1">
              Wealth Intelligence Platform
            </div>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-5">
          {nav.map((group) => (
            <div key={group.title}>
              <div className="px-2 text-[10px] font-semibold uppercase tracking-wider text-navy-200/50 mb-1.5">
                {group.title}
              </div>
              <div className="space-y-0.5">
                {group.items.map((item) => {
                  const active = pathname === item.href || (item.href !== "/studio" && pathname.startsWith(item.href + "/"));
                  const Icon = item.icon;
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className={`flex items-center gap-3 px-2.5 py-2 rounded-lg text-sm transition ${
                        active ? "bg-white/10 text-white" : "text-navy-200/80 hover:bg-white/5 hover:text-white"
                      }`}
                    >
                      <Icon size={17} strokeWidth={1.9} />
                      {item.label}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="relative p-3 border-t border-white/5 space-y-1">
          {/* Notification dropdown */}
          {showNotifs && (
            <div className="absolute bottom-full left-3 right-3 mb-1 bg-navy-800 border border-white/10 rounded-xl shadow-xl z-50 overflow-hidden">
              <div className="px-3 py-2 border-b border-white/10 text-xs font-semibold text-navy-200/70 uppercase tracking-wider">
                Notifications
              </div>
              {notifItems.length === 0 ? (
                <div className="px-3 py-4 text-xs text-navy-200/50 text-center">All clear</div>
              ) : (
                <div className="max-h-64 overflow-y-auto divide-y divide-white/5">
                  {notifItems.map((item: any) => (
                    <div key={item.id} className="px-3 py-2.5 hover:bg-white/5">
                      <div className="flex items-start gap-2">
                        {item.type === "surveillance" ? (
                          <AlertTriangle size={13} className="mt-0.5 shrink-0 text-amber-400" />
                        ) : (
                          <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-blue-400" />
                        )}
                        <div className="min-w-0">
                          <div className="text-xs text-white/90 leading-snug truncate">{item.title}</div>
                          {item.summary && (
                            <div className="text-[11px] text-navy-200/50 truncate mt-0.5">{item.summary}</div>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <RoleSwitcher />
          <div className="flex items-center gap-3 px-2 py-2">
            <div className="h-8 w-8 rounded-full bg-gold/20 text-gold-soft flex items-center justify-center text-sm font-semibold">
              {(user.full_name || "U").slice(0, 1)}
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-sm text-white truncate">{user.full_name}</div>
              <div className="text-[11px] text-navy-200/60 truncate capitalize">
                {user.title || user.role}
              </div>
            </div>
            {!isClient && (
              <button
                onClick={() => setShowNotifs((v) => !v)}
                className="relative text-navy-200/60 hover:text-white"
                title="Notifications"
              >
                <Bell size={15} />
                {notifCount > 0 && (
                  <span className="absolute -top-1.5 -right-1.5 h-3.5 w-3.5 rounded-full bg-red-500 text-[9px] text-white flex items-center justify-center font-bold">
                    {notifCount > 9 ? "9+" : notifCount}
                  </span>
                )}
              </button>
            )}
            <button onClick={openMfa} className={`relative text-navy-200/60 hover:text-white ${user.mfa_enabled ? "text-green-400/80" : ""}`} title="MFA / Two-factor auth">
              <Shield size={15} />
              {user.mfa_enabled && (
                <span className="absolute -top-1 -right-1 h-2 w-2 rounded-full bg-green-400" />
              )}
            </button>
            <button onClick={() => setShowChangePwd(true)} className="text-navy-200/60 hover:text-white" title="Change password">
              <KeyRound size={15} />
            </button>
            <button onClick={logout} className="text-navy-200/60 hover:text-white" title="Sign out">
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 ml-64 min-h-screen">
        <div className="max-w-[1280px] mx-auto px-8 py-7 fade-in">{children}</div>
      </main>

      {/* MFA modal */}
      {showMfa && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="card w-full max-w-sm p-6 space-y-4">
            <div className="font-semibold text-ink flex items-center gap-2">
              <Shield size={16} className={user.mfa_enabled ? "text-green-500" : "text-ink-muted"} />
              Two-factor authentication
            </div>

            {mfaStep === "info" && (
              <div className="space-y-3">
                {user.mfa_enabled ? (
                  <>
                    <div className="flex items-center gap-2 text-sm text-green-600 bg-green-50 rounded-lg px-3 py-2">
                      <CheckCircle2 size={14} /> MFA is enabled on your account.
                    </div>
                    {mfaMsg && <p className="text-sm text-ink-muted">{mfaMsg}</p>}
                    <p className="text-sm text-ink-muted">To disable MFA, enter a code from your authenticator app.</p>
                    <button className="btn-outline text-sm w-full" onClick={() => { setMfaStep("disable"); setMfaMsg(""); }}>
                      Disable MFA…
                    </button>
                  </>
                ) : (
                  <>
                    <p className="text-sm text-ink-muted">
                      Protect your account with a time-based one-time password (TOTP). Works with Google Authenticator, Authy, 1Password, and others.
                    </p>
                    {mfaMsg && <p className="text-sm text-positive">{mfaMsg}</p>}
                    <button className="btn-primary w-full" onClick={startMfaSetup} disabled={mfaBusy}>
                      {mfaBusy ? "Generating…" : "Enable MFA"}
                    </button>
                  </>
                )}
                <button className="btn-outline w-full" onClick={() => setShowMfa(false)}>Close</button>
              </div>
            )}

            {mfaStep === "setup" && (
              <div className="space-y-3">
                <p className="text-sm text-ink-muted">
                  Add a new account in your authenticator app and enter the secret key below.
                </p>
                <div>
                  <label className="label">Secret key</label>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 bg-navy-50 rounded-lg px-3 py-2 text-sm font-mono tracking-wider text-ink break-all">{mfaSecret}</code>
                    <button onClick={copySecret} className="btn-ghost p-2 shrink-0" title="Copy secret">
                      {copied ? <Check size={14} className="text-positive" /> : <Copy size={14} />}
                    </button>
                  </div>
                  <p className="text-[11px] text-ink-muted mt-1">Or open on mobile: <a href={mfaUri} className="underline text-gold">tap here</a></p>
                </div>
                <div>
                  <label className="label">Verify — enter the 6-digit code from your app</label>
                  <input
                    className="input text-center text-xl tracking-widest"
                    type="text" inputMode="numeric" maxLength={6} placeholder="000000"
                    value={mfaCode} onChange={(e) => setMfaCode(e.target.value.replace(/\D/g, ""))}
                    autoFocus
                  />
                </div>
                {mfaMsg && <p className="text-sm text-critical">{mfaMsg}</p>}
                <div className="flex gap-2">
                  <button className="btn-primary flex-1" onClick={confirmMfa} disabled={mfaBusy || mfaCode.length < 6}>
                    {mfaBusy ? "Verifying…" : "Activate MFA"}
                  </button>
                  <button className="btn-outline" onClick={() => { setMfaStep("info"); setMfaMsg(""); }}>Back</button>
                </div>
              </div>
            )}

            {mfaStep === "disable" && (
              <div className="space-y-3">
                <p className="text-sm text-ink-muted">Enter the current code from your authenticator app to disable MFA.</p>
                <input
                  className="input text-center text-xl tracking-widest"
                  type="text" inputMode="numeric" maxLength={6} placeholder="000000"
                  value={mfaCode} onChange={(e) => setMfaCode(e.target.value.replace(/\D/g, ""))}
                  autoFocus
                />
                {mfaMsg && <p className="text-sm text-critical">{mfaMsg}</p>}
                <div className="flex gap-2">
                  <button className="btn-primary flex-1 bg-critical/90 hover:bg-critical" onClick={disableMfa} disabled={mfaBusy || mfaCode.length < 6}>
                    {mfaBusy ? "Disabling…" : "Disable MFA"}
                  </button>
                  <button className="btn-outline" onClick={() => { setMfaStep("info"); setMfaMsg(""); setMfaCode(""); }}>Cancel</button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Change password modal */}
      {showChangePwd && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="card w-full max-w-sm p-6 space-y-4">
            <div className="font-semibold text-ink flex items-center gap-2"><KeyRound size={16} /> Change password</div>
            {pwdOk ? (
              <p className="text-sm text-positive">Password updated.</p>
            ) : (
              <form onSubmit={submitChangePwd} className="space-y-3">
                {pwdErr && <p className="text-sm text-red-600">{pwdErr}</p>}
                <div>
                  <label className="label">Current password</label>
                  <div className="relative">
                    <input className="input pr-9" type={showPwd ? "text" : "password"}
                      value={pwdForm.current} onChange={(e) => setPwdForm({ ...pwdForm, current: e.target.value })} autoFocus />
                    <button type="button" onClick={() => setShowPwd(!showPwd)}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-muted">
                      {showPwd ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                </div>
                <div>
                  <label className="label">New password</label>
                  <input className="input" type={showPwd ? "text" : "password"} placeholder="Min. 8 characters"
                    value={pwdForm.next} onChange={(e) => setPwdForm({ ...pwdForm, next: e.target.value })} />
                </div>
                <div>
                  <label className="label">Confirm new password</label>
                  <input className="input" type={showPwd ? "text" : "password"}
                    value={pwdForm.confirm} onChange={(e) => setPwdForm({ ...pwdForm, confirm: e.target.value })} />
                </div>
                <div className="flex gap-2 pt-1">
                  <button type="submit" className="btn-primary" disabled={pwdLoading}>{pwdLoading ? "Saving…" : "Update password"}</button>
                  <button type="button" className="btn-outline" onClick={() => { setShowChangePwd(false); setPwdErr(""); }}>Cancel</button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Mark({ accent = "#c8a35e" }: { accent?: string }) {
  return (
    <svg width="30" height="30" viewBox="0 0 32 32" fill="none">
      <circle cx="16" cy="16" r="15" stroke={accent} strokeWidth="1.5" />
      <path d="M16 7l6 11H10l6-11z" fill={accent} opacity="0.9" />
      <circle cx="16" cy="20" r="2.4" fill="#fff" />
    </svg>
  );
}
