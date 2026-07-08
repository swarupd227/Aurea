"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { login, api, setSession } from "@/lib/api";
import { roleLanding } from "@/lib/roles";
import { ShieldCheck } from "lucide-react";

const DEMO = [
  { role: "Adviser", email: "sophie.adviser@aurea.demo", desc: "Studio cockpit", subtitle: "Welcome back to your cockpit." },
  { role: "Compliance", email: "compliance@aurea.demo", desc: "Provenance & governance", subtitle: "Your governance dashboard." },
  { role: "Admin", email: "admin@aurea.demo", desc: "Platform configuration", subtitle: "Platform configuration." },
  { role: "Client", email: "client@aurea.demo", desc: "Canvas experience", subtitle: "Your personal wealth view." },
];

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("sophie.adviser@aurea.demo");
  const [password, setPassword] = useState("aurea");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [subtitle, setSubtitle] = useState(DEMO[0].subtitle);

  // MFA challenge state
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [mfaCode, setMfaCode] = useState("");

  async function doLogin(e?: React.FormEvent) {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await login(email, password);
      if (result.mfa_required && result.mfa_token) {
        setMfaToken(result.mfa_token);
        setBusy(false);
        return;
      }
      if (result.user) {
        router.push(roleLanding(result.user.role));
      }
    } catch (err: any) {
      setError(err.message || "Login failed");
    } finally {
      setBusy(false);
    }
  }

  async function doMfaVerify(e?: React.FormEvent) {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const data = await api<{ access_token: string; user: any }>("/api/auth/mfa/verify", {
        body: { mfa_token: mfaToken, code: mfaCode.trim() },
      });
      setSession(data.access_token, data.user);
      router.push(roleLanding(data.user.role));
    } catch (err: any) {
      setError(err.message || "Verification failed");
    } finally {
      setBusy(false);
    }
  }

  async function pickPersona(d: typeof DEMO[0]) {
    setEmail(d.email);
    setPassword("aurea");
    setSubtitle(d.subtitle);
    setBusy(true);
    setError(null);
    setMfaToken(null);
    setMfaCode("");
    try {
      const result = await login(d.email, "aurea");
      if (result.mfa_required && result.mfa_token) {
        setMfaToken(result.mfa_token);
        setBusy(false);
        return;
      }
      if (result.user) {
        router.push(roleLanding(result.user.role));
      }
    } catch (err: any) {
      setError(err.message || "Login failed");
      setBusy(false);
    }
  }

  // MFA challenge view
  if (mfaToken) {
    return (
      <div className="min-h-screen grid lg:grid-cols-2">
        <div className="hidden lg:flex flex-col justify-between bg-navy-900 text-white p-12 relative overflow-hidden">
          <div
            className="absolute inset-0 opacity-20"
            style={{ background: "radial-gradient(700px circle at 20% 10%, #2a5575, transparent 60%), radial-gradient(600px circle at 80% 90%, #c8a35e44, transparent 55%)" }}
          />
          <div className="relative">
            <div className="flex items-center gap-3">
              <Mark />
              <span className="text-2xl font-semibold tracking-tight">Aurea</span>
            </div>
          </div>
          <div className="relative max-w-md">
            <ShieldCheck size={40} className="text-gold mb-4 opacity-80" />
            <h1 className="font-serif text-4xl leading-tight">Two-factor authentication</h1>
            <p className="mt-4 text-navy-200 text-lg">Your account is protected with TOTP-based MFA.</p>
          </div>
          <div className="relative text-navy-200/70 text-xs flex gap-4">
            {["Aurea Core", "Atlas", "Studio", "Canvas", "Provenance", "Conduit"].map((c) => (
              <span key={c}>{c}</span>
            ))}
          </div>
        </div>

        <div className="flex items-center justify-center p-8 bg-paper">
          <div className="w-full max-w-sm">
            <div className="flex items-center gap-2 mb-6 text-navy-800">
              <ShieldCheck size={22} className="text-gold" />
              <span className="text-xl font-semibold text-ink">Verify your identity</span>
            </div>
            <p className="text-sm text-ink-muted mb-6">
              Open your authenticator app and enter the 6-digit code for <strong>Aurea</strong>.
            </p>
            <form onSubmit={doMfaVerify} className="space-y-4">
              <div>
                <label className="label">Verification code</label>
                <input
                  className="input text-center text-2xl tracking-widest"
                  type="text"
                  inputMode="numeric"
                  maxLength={6}
                  placeholder="000000"
                  value={mfaCode}
                  onChange={(e) => setMfaCode(e.target.value.replace(/\D/g, ""))}
                  autoFocus
                />
              </div>
              {error && <div className="text-sm text-critical bg-critical/5 rounded-lg px-3 py-2">{error}</div>}
              <button className="btn-primary w-full" disabled={busy || mfaCode.length < 6}>
                {busy ? "Verifying…" : "Verify"}
              </button>
              <button
                type="button"
                className="btn-outline w-full"
                onClick={() => { setMfaToken(null); setMfaCode(""); setError(null); }}
              >
                Back to login
              </button>
            </form>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen grid lg:grid-cols-2">
      {/* Brand panel */}
      <div className="hidden lg:flex flex-col justify-between bg-navy-900 text-white p-12 relative overflow-hidden">
        <div
          className="absolute inset-0 opacity-20"
          style={{ background: "radial-gradient(700px circle at 20% 10%, #2a5575, transparent 60%), radial-gradient(600px circle at 80% 90%, #c8a35e44, transparent 55%)" }}
        />
        <div className="relative">
          <div className="flex items-center gap-3">
            <Mark />
            <span className="text-2xl font-semibold tracking-tight">Aurea</span>
          </div>
        </div>
        <div className="relative max-w-md">
          <h1 className="font-serif text-4xl leading-tight">The Wealth Intelligence Platform</h1>
          <p className="mt-4 text-navy-200 text-lg">Truly personal advice, at scale.</p>
          <p className="mt-6 text-navy-200/80 text-sm leading-relaxed">
            A governed agentic workforce over one unified client brain — with the adviser
            unmistakably in command. Every recommendation shows its reasoning, its sources and its
            confidence, recorded to an immutable decision ledger.
          </p>
        </div>
        <div className="relative text-navy-200/70 text-xs flex gap-4">
          {["Aurea Core", "Atlas", "Studio", "Canvas", "Provenance", "Conduit"].map((c) => (
            <span key={c}>{c}</span>
          ))}
        </div>
      </div>

      {/* Form */}
      <div className="flex items-center justify-center p-8 bg-paper">
        <div className="w-full max-w-sm">
          <div className="lg:hidden flex items-center gap-2 mb-8 text-navy-800">
            <Mark dark /> <span className="text-xl font-semibold">Aurea</span>
          </div>
          <h2 className="text-2xl font-semibold text-ink">Sign in</h2>
          <p className="text-sm text-ink-muted mt-1">{subtitle}</p>

          <form onSubmit={doLogin} className="mt-6 space-y-4">
            <div>
              <label className="label">Email</label>
              <input className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
            <div>
              <label className="label">Password</label>
              <input
                className="input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            {error && <div className="text-sm text-critical bg-critical/5 rounded-lg px-3 py-2">{error}</div>}
            <button className="btn-primary w-full" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <div className="mt-8">
            <div className="text-xs font-semibold uppercase tracking-wide text-ink-muted mb-2">
              Demo personas — click to sign in instantly
            </div>
            <div className="grid grid-cols-2 gap-2">
              {DEMO.map((d) => (
                <button
                  key={d.email}
                  onClick={() => pickPersona(d)}
                  disabled={busy}
                  className="text-left rounded-lg border border-navy-100 px-3 py-2 hover:border-gold/50 hover:bg-gold-soft/10 transition disabled:opacity-50"
                >
                  <div className="text-sm font-medium text-ink">{d.role}</div>
                  <div className="text-xs text-ink-muted">{d.desc}</div>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Mark({ dark = false }: { dark?: boolean }) {
  return (
    <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
      <circle cx="16" cy="16" r="15" stroke={dark ? "#163a52" : "#c8a35e"} strokeWidth="1.5" />
      <path d="M16 7l6 11H10l6-11z" fill={dark ? "#163a52" : "#c8a35e"} opacity="0.9" />
      <circle cx="16" cy="20" r="2.4" fill={dark ? "#fff" : "#0f2b3d"} />
    </svg>
  );
}
