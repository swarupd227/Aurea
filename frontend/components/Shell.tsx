"use client";
import { ReactNode, Suspense, useEffect, useRef, useState } from "react";
import { PageSectionTabs } from "./SectionTabs";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Sparkles, LogOut, PiggyBank, Target, KeyRound, Eye, EyeOff, Bell, AlertTriangle, CheckCircle2,
  Shield, Copy, Check, MessageSquare, ChevronRight, Search, Menu, X, Bot,
} from "lucide-react";
import { clearSession, getToken, getUser } from "@/lib/api";
import { isEntryActive, moreViews, pinnedViews, viewsForRole, hasWorkspace, type View } from "@/lib/nav";
import RoleSwitcher from "./RoleSwitcher";
import PageAsk from "./PageAsk";
import { CommandPalette } from "./CommandPalette";
import { BrandMark } from "./BrandMark";
import OutsideAgentsDialog from "./OutsideAgentsDialog";

// The pages ("Views") live in lib/nav.ts, shared with the Astra workspace rail and the palette.
// The conversation is the home; the sidebar carries a role's handful of everyday views and tucks
// the rest under "More views" - every one is still a keystroke away in the palette (Ctrl/Cmd+K).

// The same rows, sections and colours as the workspace rail (app/workspace/components/Rail.tsx),
// so a classic page and the conversation read as one product.
const rowBase =
  "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";
const rowIdle = "text-ink-soft hover:bg-border-soft hover:text-ink";
const sectionTitle = "px-2 pb-1 font-mono text-[11px] uppercase tracking-wider text-ink-muted";
const iconButton =
  "rounded p-1 text-ink-muted transition-colors hover:bg-border-soft hover:text-ink " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

function ViewLink({ item, pathname }: { item: View; pathname: string }) {
  const active = isEntryActive(pathname, item);
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={`${rowBase} ${active ? "bg-border-soft text-ink" : rowIdle}`}
    >
      <Icon className="h-4 w-4 shrink-0 text-ink-muted" aria-hidden />
      <span className="truncate">{item.label}</span>
    </Link>
  );
}

/** A list without repeats: a view or a notification listed twice is one row, never two with one key. */
function unique<T>(items: T[], key: (item: T) => string): T[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const k = key(item);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
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
  const [showAgents, setShowAgents] = useState(false);
  const [notifItems, setNotifItems] = useState<any[]>([]);
  const [notifCount, setNotifCount] = useState(0);

  // Below md the sidebar is a drawer behind a menu button in a top bar.
  const [navOpen, setNavOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const drawerClose = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);

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
    if (!user || user.role === "client" || user.role === "superadmin") return;
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

  // A page chosen from the drawer closes it.
  useEffect(() => {
    setNavOpen(false);
  }, [pathname]);

  // While the drawer is open: Escape closes it, the page behind does not scroll, and focus moves into
  // it; closed again, focus goes back to the menu button.
  useEffect(() => {
    if (!navOpen) {
      if (wasOpen.current) menuButton.current?.focus();
      wasOpen.current = false;
      return;
    }
    wasOpen.current = true;
    drawerClose.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setNavOpen(false);
    };
    const onResize = () => {
      if (window.matchMedia("(min-width: 768px)").matches) setNavOpen(false);
    };
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    return () => {
      document.body.style.overflow = overflow;
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
    };
  }, [navOpen]);

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
  // A client converses on their own Canvas pages, with their own read-only tools: no workspace, no rail.
  const showDock = (isStaff && showAstra && user.role !== "admin") || (isClient && pathname.startsWith("/canvas"));
  const pinned = unique(isStaff ? pinnedViews(user.role) : isClient ? clientViews : viewsForRole(user.role), (v) => v.href);
  const more = isStaff ? moreViews(user.role) : [];
  const moreActive = more.some((g) => g.items.some((i) => isEntryActive(pathname, i)));
  const moreCount = more.reduce((n, g) => n + g.items.length, 0);

  const notifications = unique(notifItems, (n: any) => `${n.type ?? ""}:${n.id}`);
  const brand = <BrandMark accent={branding.accent} />;

  return (
    <div className="min-h-screen bg-paper text-ink">
      {/* Below md: a top bar, and the sidebar becomes a drawer behind its menu button. */}
      <header className="sticky top-0 z-30 flex h-12 items-center gap-2 border-b border-border bg-paper px-3 md:hidden">
        <button
          ref={menuButton}
          type="button"
          data-testid="shell-menu"
          onClick={() => setNavOpen(true)}
          aria-label="Open the menu"
          aria-expanded={navOpen}
          aria-controls="shell-sidebar"
          className={iconButton}
        >
          <Menu className="h-5 w-5" aria-hidden />
        </button>
        {brand}
        <span className="min-w-0 flex-1 truncate text-[15px] font-semibold tracking-tight text-ink">Astra for Wealth</span>
        {showAstra && (
          <button
            type="button"
            onClick={() => window.dispatchEvent(new Event("astra:open-palette"))}
            aria-label="Search or ask"
            className={iconButton}
          >
            <Search className="h-4 w-4" aria-hidden />
          </button>
        )}
      </header>

      {navOpen && (
        <div aria-hidden className="fixed inset-0 z-40 bg-black/50 md:hidden" onClick={() => setNavOpen(false)} />
      )}

      {/* Sidebar: a fixed column from md, a drawer below it (off screen and out of the tab order when shut). */}
      <aside
        id="shell-sidebar"
        aria-label="Navigation"
        className={`fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] flex-col border-r border-border bg-surface text-ink transition-[transform,visibility] duration-200 motion-reduce:transition-none md:z-30 md:w-64 md:max-w-none md:translate-x-0 md:visible ${
          navOpen ? "visible translate-x-0 shadow-lift" : "invisible -translate-x-full"
        }`}
      >
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
          {brand}
          <div className="min-w-0 flex-1">
            <div className="truncate text-[15px] font-semibold tracking-tight text-ink">Astra for Wealth</div>
          </div>
          <button
            ref={drawerClose}
            type="button"
            onClick={() => setNavOpen(false)}
            aria-label="Close the menu"
            className={`${iconButton} md:hidden`}
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>
        {user.firm?.demonstration && (
          <div className="px-3 pt-2">
            <div className="inline-block rounded border border-border px-1.5 py-0.5 text-[10px] text-ink-muted" data-testid="demonstration-firm">
              Demonstration · invented clients
            </div>
          </div>
        )}

        <nav className="min-h-0 flex-1 space-y-5 overflow-y-auto px-2 py-3" data-testid="shell-nav">
          {showAstra && (
            <div className="space-y-0.5">
              <Link
                href="/workspace"
                data-testid="nav-astra"
                className={`${rowBase} bg-border-soft font-medium text-ink hover:text-ink`}
              >
                <MessageSquare className="h-4 w-4 shrink-0 text-ink-muted" aria-hidden />
                Talk to Astra
              </Link>
              <button
                type="button"
                data-testid="nav-search"
                onClick={() => window.dispatchEvent(new Event("astra:open-palette"))}
                className={`${rowBase} ${rowIdle}`}
              >
                <Search className="h-4 w-4 shrink-0 text-ink-muted" aria-hidden />
                Search or ask
                <kbd className="ml-auto font-mono text-[10px] text-ink-muted">Ctrl K</kbd>
              </button>
            </div>
          )}

          <section>
            {isStaff && <h2 className={sectionTitle}>Views</h2>}
            <div className="space-y-0.5">
              {pinned.map((item) => (
                <ViewLink key={item.href} item={item} pathname={pathname} />
              ))}
            </div>
          </section>

          {more.length > 0 && (
            <details key={moreActive ? "open" : "closed"} open={moreActive} className="group" data-testid="nav-more-views">
              <summary className={`${rowBase} cursor-pointer list-none text-xs text-ink-muted hover:bg-border-soft hover:text-ink [&::-webkit-details-marker]:hidden`}>
                <ChevronRight className="h-3.5 w-3.5 transition-transform group-open:rotate-90" aria-hidden />
                More views
                <span className="ml-auto font-mono tabular-nums">{moreCount}</span>
              </summary>
              <div className="mt-1 space-y-3 pb-1">
                {more.map((group) => (
                  <div key={group.intent}>
                    <div className="px-2 pb-0.5 font-mono text-[10px] uppercase tracking-wider text-ink-faint">{group.title}</div>
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

        <div className="relative shrink-0 space-y-0.5 border-t border-border p-2">
          {/* Notification dropdown */}
          {showNotifs && (
            <div className="absolute bottom-full left-2 right-2 z-50 mb-1 overflow-hidden rounded-lg border border-border bg-surface shadow-lift" role="region" aria-label="Notifications">
              <div className="border-b border-border px-3 py-2 font-mono text-[11px] uppercase tracking-wider text-ink-muted">
                Notifications
              </div>
              {notifications.length === 0 ? (
                <div className="px-3 py-4 text-center text-xs text-ink-muted">All clear</div>
              ) : (
                <div className="max-h-64 divide-y divide-border-soft overflow-y-auto">
                  {notifications.map((item: any) => (
                    <div key={`${item.type ?? ""}:${item.id}`} className="px-3 py-2.5 hover:bg-border-soft">
                      <div className="flex items-start gap-2">
                        {item.type === "surveillance" ? (
                          <AlertTriangle size={13} className="mt-0.5 shrink-0 text-warn" aria-hidden />
                        ) : (
                          <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-info" aria-hidden />
                        )}
                        <div className="min-w-0">
                          <div className="truncate text-xs leading-snug text-ink">{item.title}</div>
                          {item.summary && (
                            <div className="mt-0.5 truncate text-[11px] text-ink-muted">{item.summary}</div>
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
          <div className="flex items-center gap-1.5 px-2 py-1.5">
            <div aria-hidden className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-border-soft text-xs font-semibold text-ink-soft">
              {(user.full_name || "U").slice(0, 1)}
            </div>
            <div className="min-w-0 flex-1 pl-0.5">
              <div className="truncate text-sm text-ink">{user.full_name}</div>
              <div className="truncate text-[11px] capitalize text-ink-muted">
                {user.title || user.role}
              </div>
            </div>
            {!isClient && (
              <button
                type="button"
                onClick={() => setShowNotifs((v) => !v)}
                className={`relative ${iconButton}`}
                title="Notifications"
                aria-label={notifCount > 0 ? `Notifications, ${notifCount} new` : "Notifications"}
                aria-expanded={showNotifs}
              >
                <Bell size={15} aria-hidden />
                {notifCount > 0 && (
                  <span aria-hidden className="absolute -right-1 -top-1 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-crit text-[9px] font-bold text-white">
                    {notifCount > 9 ? "9+" : notifCount}
                  </span>
                )}
              </button>
            )}
            <button
              type="button"
              onClick={openMfa}
              className={`relative ${user.mfa_enabled ? iconButton.replace("text-ink-muted", "text-ok") : iconButton}`}
              title="MFA / Two-factor auth"
              aria-label={user.mfa_enabled ? "Two-factor authentication, on" : "Two-factor authentication"}
            >
              <Shield size={15} aria-hidden />
              {user.mfa_enabled && (
                <span aria-hidden className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-ok" />
              )}
            </button>
            {isStaff && (
              <button type="button" onClick={() => setShowAgents(true)} className={iconButton} title="Outside agents"
                aria-label="Outside agents" data-testid="outside-agents-open">
                <Bot size={15} aria-hidden />
              </button>
            )}
            <button type="button" onClick={() => setShowChangePwd(true)} className={iconButton} title="Change password" aria-label="Change password">
              <KeyRound size={15} aria-hidden />
            </button>
            <button type="button" onClick={logout} className={iconButton} title="Sign out" aria-label="Sign out">
              <LogOut size={15} aria-hidden />
            </button>
          </div>
        </div>
      </aside>

      {/* Main */}
      <main className="min-h-screen min-w-0 md:ml-64">
        <div className="mx-auto max-w-[1280px] px-4 py-5 fade-in md:px-8 md:py-7">
          {isStaff && (
            <Suspense fallback={null}>
              <PageSectionTabs role={user.role} />
            </Suspense>
          )}
          {children}
        </div>
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
              <Shield size={16} className={user.mfa_enabled ? "text-ok" : "text-ink-muted"} aria-hidden />
              Two-factor authentication
            </div>

            {mfaStep === "info" && (
              <div className="space-y-3">
                {user.mfa_enabled ? (
                  <>
                    <div className="flex items-center gap-2 text-sm text-ok bg-ok-bg rounded-lg px-3 py-2">
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
                    {mfaMsg && <p className="text-sm text-ok">{mfaMsg}</p>}
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
                    <code className="flex-1 bg-border-soft rounded-lg px-3 py-2 text-sm font-mono tracking-wider text-ink break-all">{mfaSecret}</code>
                    <button onClick={copySecret} className="btn-ghost p-2 shrink-0" title="Copy secret" aria-label="Copy secret">
                      {copied ? <Check size={14} className="text-ok" /> : <Copy size={14} />}
                    </button>
                  </div>
                  <p className="text-[11px] text-ink-muted mt-1">Or open on mobile: <a href={mfaUri} className="underline text-ink">tap here</a></p>
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
                {mfaMsg && <p className="text-sm text-crit">{mfaMsg}</p>}
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
                {mfaMsg && <p className="text-sm text-crit">{mfaMsg}</p>}
                <div className="flex gap-2">
                  <button className="btn-crit flex-1" onClick={disableMfa} disabled={mfaBusy || mfaCode.length < 6}>
                    {mfaBusy ? "Disabling…" : "Disable MFA"}
                  </button>
                  <button className="btn-outline" onClick={() => { setMfaStep("info"); setMfaMsg(""); setMfaCode(""); }}>Cancel</button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {showAgents && <OutsideAgentsDialog onClose={() => setShowAgents(false)} />}

      {/* Change password modal */}
      {showChangePwd && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="card w-full max-w-sm p-6 space-y-4">
            <div className="font-semibold text-ink flex items-center gap-2"><KeyRound size={16} /> Change password</div>
            {pwdOk ? (
              <p className="text-sm text-ok">Password updated.</p>
            ) : (
              <form onSubmit={submitChangePwd} className="space-y-3">
                {pwdErr && <p className="text-sm text-crit" role="alert">{pwdErr}</p>}
                <div>
                  <label className="label">Current password</label>
                  <div className="relative">
                    <input className="input pr-9" type={showPwd ? "text" : "password"}
                      value={pwdForm.current} onChange={(e) => setPwdForm({ ...pwdForm, current: e.target.value })} autoFocus />
                    <button type="button" onClick={() => setShowPwd(!showPwd)}
                      aria-label={showPwd ? "Hide the passwords" : "Show the passwords"}
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

