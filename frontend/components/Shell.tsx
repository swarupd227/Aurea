"use client";
import { ReactNode, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Sparkles, LogOut, PiggyBank, Target, KeyRound, Eye, EyeOff, Bell, AlertTriangle, CheckCircle2,
  Shield, Copy, Check, MessageSquare, ChevronRight, Search,
} from "lucide-react";
import { clearSession, getToken, getUser } from "@/lib/api";
import { isViewActive, moreViews, pinnedViews, viewsForRole, hasWorkspace, type View } from "@/lib/nav";
import RoleSwitcher from "./RoleSwitcher";
import PageAsk from "./PageAsk";
import { CommandPalette } from "./CommandPalette";

// The pages ("Views") live in lib/nav.ts, shared with the Astra workspace rail and the palette.
// The conversation is the home; the sidebar carries a role's handful of everyday views and tucks
// the rest under "More views" - every one is still a keystroke away in the palette (Ctrl/Cmd+K).

function ViewLink({ item, pathname }: { item: View; pathname: string }) {
  const active = isViewActive(pathname, item.href);
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      className={`flex items-center gap-3 px-2.5 py-2 rounded-lg text-sm transition ${
        active ? "bg-white/10 text-white" : "text-navy-200/80 hover:bg-white/5 hover:text-white"
      }`}
    >
      <Icon size={17} strokeWidth={1.9} />
      {item.label}
    </Link>
  );
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

  // Clients see their own three views; the platform owner sees one; staff get the conversation,
  // a handful of pinned views for their role, and the rest under "More views".
  const clientViews: View[] = [
    { href: "/canvas", label: "My wealth", icon: Sparkles, intent: "clients" },
    { href: "/canvas/retirement", label: "My retirement", icon: PiggyBank, intent: "clients" },
    { href: "/canvas/goals", label: "My goals", icon: Target, intent: "clients" },
  ];
  const isStaff = !isClient && user.role !== "superadmin";
  const showAstra = hasWorkspace(user.role);
  // Admin pages are configuration forms, not decisions; the dock stays off them.
  const showDock = isStaff && showAstra && user.role !== "admin";
  const pinned = isStaff ? pinnedViews(user.role) : isClient ? clientViews : viewsForRole(user.role);
  const more = isStaff ? moreViews(user.role) : [];
  const moreActive = more.some((g) => g.items.some((i) => isViewActive(pathname, i.href)));
  const moreCount = more.reduce((n, g) => n + g.items.length, 0);

  return (
    <div className="min-h-screen flex bg-paper">
      {/* Sidebar */}
      <aside className="w-64 shrink-0 bg-navy-900 text-navy-100 flex flex-col fixed h-screen">
        <div className="px-5 py-5 flex items-center gap-2.5 border-b border-white/5">
          <Mark accent={branding.accent} />
          <div>
            <div className="text-white font-semibold leading-none">Astra for Wealth</div>
            <div className="text-[10px] uppercase tracking-wider text-navy-200/70 mt-1">
              Wealth Intelligence Platform
            </div>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-5" data-testid="shell-nav">
          {showAstra && (
            <div className="space-y-0.5">
              <Link
                href="/workspace"
                data-testid="nav-astra"
                className="flex items-center gap-3 px-2.5 py-2 rounded-lg text-sm font-medium text-white bg-white/10 hover:bg-white/15 transition"
              >
                <MessageSquare size={17} strokeWidth={1.9} />
                Talk to Astra
              </Link>
              <button
                type="button"
                data-testid="nav-search"
                onClick={() => window.dispatchEvent(new Event("astra:open-palette"))}
                className="w-full flex items-center gap-3 px-2.5 py-2 rounded-lg text-sm text-navy-200/80 hover:bg-white/5 hover:text-white transition"
              >
                <Search size={17} strokeWidth={1.9} />
                Search or ask
                <kbd className="ml-auto font-mono text-[10px] text-navy-200/50">Ctrl K</kbd>
              </button>
            </div>
          )}

          <div>
            {isStaff && (
              <div className="px-2 text-[10px] font-semibold uppercase tracking-wider text-navy-200/50 mb-1.5">
                Views
              </div>
            )}
            <div className="space-y-0.5">
              {pinned.map((item) => (
                <ViewLink key={item.href} item={item} pathname={pathname} />
              ))}
            </div>
          </div>

          {more.length > 0 && (
            <details key={moreActive ? "open" : "closed"} open={moreActive} className="group" data-testid="nav-more-views">
              <summary className="flex cursor-pointer list-none items-center gap-2 px-2.5 py-2 rounded-lg text-sm text-navy-200/80 hover:bg-white/5 hover:text-white transition [&::-webkit-details-marker]:hidden">
                <ChevronRight size={15} className="transition-transform group-open:rotate-90" />
                More views
                <span className="ml-auto font-mono text-[11px] tabular-nums text-navy-200/50">{moreCount}</span>
              </summary>
              <div className="mt-2 space-y-4">
                {more.map((group) => (
                  <div key={group.intent}>
                    <div className="px-2 text-[10px] font-semibold uppercase tracking-wider text-navy-200/50 mb-1.5">
                      {group.title}
                    </div>
                    <div className="space-y-0.5">
                      {group.items.map((item) => (
                        <ViewLink key={item.href} item={item} pathname={pathname} />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </details>
          )}
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
        {showDock && <div aria-hidden className="h-36" />}
      </main>

      {/* A conversation docked on every page: same Astra, same tools, never replaces the page. */}
      {showDock && <PageAsk />}
      {isStaff && <CommandPalette />}

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
