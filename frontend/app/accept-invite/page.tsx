"use client";
import { useState, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { Eye, EyeOff, KeyRound, CheckCircle } from "lucide-react";
import { api } from "@/lib/api";

export default function AcceptInvitePage() {
  return <Suspense><AcceptInviteInner /></Suspense>;
}

function AcceptInviteInner() {
  const params = useSearchParams();
  const router = useRouter();
  const token = params.get("token") || "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPwd, setShowPwd] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  if (!token) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-surface p-4">
        <div className="card p-8 max-w-md w-full text-center">
          <p className="text-ink-muted">No invite token found. Check your link and try again.</p>
        </div>
      </div>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (password.length < 8) { setError("Password must be at least 8 characters."); return; }
    if (password !== confirm) { setError("Passwords do not match."); return; }
    setLoading(true);
    try {
      const data = await api("/api/auth/accept-invite", { body: { token, password } });
      if (typeof window !== "undefined") {
        localStorage.setItem("aurea_token", data.access_token);
        localStorage.setItem("aurea_user", JSON.stringify(data.user));
      }
      setDone(true);
      setTimeout(() => router.replace("/studio"), 1500);
    } catch (err: any) {
      setError(err.message || "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  if (done) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-surface p-4">
        <div className="card p-8 max-w-md w-full text-center space-y-3">
          <CheckCircle size={40} className="text-positive mx-auto" />
          <p className="font-semibold text-ink">Password set — signing you in…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface p-4">
      <div className="card p-8 max-w-md w-full space-y-6">
        <div className="text-center space-y-1">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-navy-100 mb-2">
            <KeyRound size={22} className="text-navy-800" />
          </div>
          <h1 className="text-xl font-semibold text-ink">Set your password</h1>
          <p className="text-sm text-ink-muted">Choose a password of at least 8 characters to activate your account.</p>
        </div>

        {error && (
          <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{error}</div>
        )}

        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="label">New password</label>
            <div className="relative">
              <input
                className="input pr-10"
                type={showPwd ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoFocus
                placeholder="At least 8 characters"
              />
              <button
                type="button"
                onClick={() => setShowPwd(!showPwd)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-muted hover:text-ink"
              >
                {showPwd ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </div>
          <div>
            <label className="label">Confirm password</label>
            <input
              className="input"
              type={showPwd ? "text" : "password"}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder="Repeat password"
            />
          </div>
          <button type="submit" className="btn-primary w-full" disabled={loading}>
            {loading ? "Saving…" : "Set password"}
          </button>
        </form>
      </div>
    </div>
  );
}
