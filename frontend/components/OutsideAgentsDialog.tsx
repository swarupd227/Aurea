"use client";
/** A person's outside agent keys (B-166): make one (shown once), see each one's use, revoke with a reason. The key is a
 * secret, so it is made here and never in the conversation, which reads and revokes them behind a Confirm card. */
import { useEffect, useState } from "react";
import { Bot, Check, Copy } from "lucide-react";
import { API_BASE, api } from "@/lib/api";
import { OutsideAgentsTable, type OutsideAgentKey } from "@/app/workspace/components/artifacts/OutsideAgentsCard";

interface Listing { on: boolean; can_make: boolean; max_days: number; endpoint: string; keys: OutsideAgentKey[] }

export default function OutsideAgentsDialog({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<Listing | null>(null);
  const [label, setLabel] = useState("");
  const [days, setDays] = useState(30);
  const [made, setMade] = useState<{ label: string; key: string } | null>(null);
  const [revoking, setRevoking] = useState<OutsideAgentKey | null>(null);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  async function load() {
    try {
      setData(await api<Listing>("/api/outside-agents"));
    } catch (e: any) {
      setErr(e?.message || "Could not load the keys.");
    }
  }

  useEffect(() => {
    load();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  async function make(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await api<{ label: string; key: string }>("/api/outside-agents", { body: { label, days } });
      setMade({ label: r.label, key: r.key });
      setLabel("");
      await load();
    } catch (e: any) {
      setErr(e?.message || "The key was not made.");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(e: React.FormEvent) {
    e.preventDefault();
    if (!revoking) return;
    setBusy(true);
    setErr("");
    try {
      await api(`/api/outside-agents/${revoking.id}/revoke`, { body: { reason } });
      setRevoking(null);
      setReason("");
      await load();
    } catch (e: any) {
      setErr(e?.message || "The key was not revoked.");
    } finally {
      setBusy(false);
    }
  }

  function copy() {
    if (!made) return;
    navigator.clipboard?.writeText(made.key).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => {});
  }

  const endpoint = `${API_BASE.replace(/\/$/, "")}${data?.endpoint || "/api/mcp"}`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm"
      role="dialog" aria-modal="true" aria-labelledby="outside-agents-title" data-testid="outside-agents-dialog">
      <div className="card max-h-[90vh] w-full max-w-2xl space-y-4 overflow-y-auto p-6">
        <div className="flex flex-wrap items-center gap-2">
          <Bot size={16} className="text-ink-muted" aria-hidden />
          <h2 id="outside-agents-title" className="font-semibold text-ink">Outside agents</h2>
          {data && <span className={`chip ${data.on ? "text-ok" : "text-ink-muted"}`}>{data.on ? "on for the firm" : "off for the firm"}</span>}
        </div>

        <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 text-sm">
          <dt className="text-ink-muted">MCP endpoint</dt>
          <dd className="break-all font-mono text-xs text-ink" data-testid="outside-agents-endpoint">{endpoint}</dd>
          <dt className="text-ink-muted">Sign-in</dt>
          <dd className="font-mono text-xs text-ink">Authorization: Bearer &lt;key&gt;</dd>
          <dt className="text-ink-muted">Changes</dt>
          <dd className="text-ink">Confirm card, in your name</dd>
        </dl>

        {err && <p className="text-sm text-crit" role="alert">{err}</p>}

        {made && (
          <div className="space-y-2 rounded-lg border border-border p-3" data-testid="outside-agent-made">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-ink">{made.label}</span>
              <span className="chip text-warn">shown once</span>
            </div>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 break-all rounded bg-border-soft px-2 py-1 font-mono text-xs text-ink">{made.key}</code>
              <button type="button" className="btn-outline px-2 py-1" onClick={copy} aria-label="Copy the key">
                {copied ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />}
              </button>
            </div>
            <button type="button" className="btn-outline text-xs" onClick={() => setMade(null)}>Done</button>
          </div>
        )}

        {data && data.on && data.can_make && !made && (
          <form onSubmit={make} className="flex flex-wrap items-end gap-2">
            <div className="min-w-[12rem] flex-1">
              <label className="label" htmlFor="oa-label">Agent name</label>
              <input id="oa-label" className="input" value={label} maxLength={120} onChange={(e) => setLabel(e.target.value)}
                data-testid="outside-agent-label" />
            </div>
            <div className="w-24">
              <label className="label" htmlFor="oa-days">Days</label>
              <input id="oa-days" className="input tabular-nums" type="number" min={1} max={data.max_days} value={days}
                onChange={(e) => setDays(Number(e.target.value))} />
            </div>
            <button type="submit" className="btn-primary" disabled={busy || !label.trim()} data-testid="outside-agent-make">
              Make a key
            </button>
          </form>
        )}

        {revoking && (
          <form onSubmit={revoke} className="space-y-2 rounded-lg border border-border p-3">
            <div className="text-sm text-ink">Revoke {revoking.label} <span className="font-mono text-xs text-ink-muted">…{revoking.key_ends}</span></div>
            <label className="label" htmlFor="oa-reason">Reason</label>
            <input id="oa-reason" className="input" value={reason} maxLength={400} onChange={(e) => setReason(e.target.value)} autoFocus />
            <div className="flex gap-2">
              <button type="submit" className="btn-primary" disabled={busy || !reason.trim()}>Revoke</button>
              <button type="button" className="btn-outline" onClick={() => { setRevoking(null); setReason(""); }}>Cancel</button>
            </div>
          </form>
        )}

        {data && data.keys.length > 0 && (
          <div className="overflow-hidden rounded-lg border border-border">
            <OutsideAgentsTable rows={data.keys} onRevoke={(k) => { setRevoking(k); setReason(""); }} />
          </div>
        )}

        <div className="flex justify-end">
          <button type="button" className="btn-outline" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
