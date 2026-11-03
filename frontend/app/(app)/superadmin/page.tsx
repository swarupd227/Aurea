"use client";
import { useEffect, useState } from "react";
import { Building2, Plus, CheckCircle2, Copy, Check, Power, RefreshCw } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api, getUser } from "@/lib/api";
import { useRouter } from "next/navigation";

const DOMAINS = [
  { value: "NZ", label: "New Zealand (FMA)" },
  { value: "AU", label: "Australia (ASIC)" },
  { value: "UK", label: "United Kingdom (FCA)" },
  { value: "US", label: "United States (SEC)" },
  { value: "SG", label: "Singapore (MAS)" },
  { value: "EU", label: "European Union (ESMA)" },
  { value: "IN", label: "India (SEBI)" },
];
const CURRENCIES = ["NZD", "AUD", "GBP", "USD", "SGD", "EUR", "INR"];
const chip = "chip bg-border-soft text-ink-soft";

export default function SuperadminPage() {
  const router = useRouter();
  const user = getUser();

  // Guard. The redirect runs after render, and every hook is called first, so leaving does not change
  // the order React sees hooks in.
  const denied = typeof window !== "undefined" && !!user && user.role !== "superadmin";
  useEffect(() => {
    if (denied) router.replace("/");
  }, [denied, router]);

  const { data: firms, loading, refetch } = useApi<any[]>(denied ? null : "/api/superadmin/firms");
  const [showNew, setShowNew] = useState(false);
  const [form, setForm] = useState({
    name: "", slug: "", legal_name: "", jurisdiction: "NZ", regulator: "FMA",
    base_currency: "NZD", admin_email: "", admin_name: "",
  });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  if (denied) return null;

  function autoSlug(name: string) {
    return name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
  }

  async function createFirm(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    setResult(null);
    try {
      const payload: any = { ...form };
      if (!payload.slug) payload.slug = autoSlug(payload.name);
      if (!payload.legal_name) delete payload.legal_name;
      const r = await api("/api/superadmin/firms", { body: payload });
      setResult(r);
      refetch();
      setForm({ name: "", slug: "", legal_name: "", jurisdiction: "NZ", regulator: "FMA", base_currency: "NZD", admin_email: "", admin_name: "" });
      setShowNew(false);
    } catch (e: any) {
      setErr(e.message || "Failed to create firm.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleStatus(firmId: string, active: boolean) {
    setTogglingId(firmId);
    try {
      await api(`/api/superadmin/firms/${firmId}/status`, { method: "PATCH", body: { is_active: active } });
      refetch();
    } catch {}
    setTogglingId(null);
  }

  function copyInvite(url: string) {
    navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div data-testid="superadmin-view">
      <PageHeader
        title="Platform · Firms"
        actions={
          <button className="btn-primary" onClick={() => { setShowNew(true); setResult(null); setErr(null); }} data-testid="superadmin-new-firm">
            <Plus size={16} /> New firm
          </button>
        }
      />

      {result && (
        <Card className="mb-5 border-ok/30 bg-ok-bg" data-testid="superadmin-created">
          <div className="flex items-start gap-3">
            <CheckCircle2 size={18} className="mt-0.5 shrink-0 text-ok" />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-medium text-ink">{result.name}</span>
                <span className="chip bg-ok-bg text-ok">created · provisioned</span>
              </div>
              <div className="tile-label mt-3">Admin invite link</div>
              <div className="mt-1 flex items-center gap-2">
                <code className="flex-1 truncate rounded bg-border-soft px-2 py-1 font-mono text-xs text-ink-soft">{result.invite_url}</code>
                <button onClick={() => copyInvite(result.invite_url)} className="btn-ghost shrink-0 p-1.5" aria-label="Copy invite link">
                  {copied ? <Check size={13} className="text-ok" /> : <Copy size={13} />}
                </button>
              </div>
            </div>
          </div>
        </Card>
      )}

      {showNew && (
        <Card className="mb-5">
          <div className="mb-4 flex items-center gap-2 font-semibold text-ink">
            <Building2 size={16} /> New firm
          </div>
          <form onSubmit={createFirm} className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="label">Firm name *</label>
                <input className="input" required value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value, slug: autoSlug(e.target.value) })} />
              </div>
              <div>
                <label className="label">Slug</label>
                <input className="input font-mono text-sm" value={form.slug}
                  onChange={(e) => setForm({ ...form, slug: e.target.value })} />
              </div>
              <div>
                <label className="label">Legal name</label>
                <input className="input" value={form.legal_name}
                  onChange={(e) => setForm({ ...form, legal_name: e.target.value })} />
              </div>
              <div>
                <label className="label">Jurisdiction</label>
                <select className="input" value={form.jurisdiction}
                  onChange={(e) => setForm({ ...form, jurisdiction: e.target.value })}>
                  {DOMAINS.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
                </select>
              </div>
              <div>
                <label className="label">Regulator</label>
                <input className="input" value={form.regulator}
                  onChange={(e) => setForm({ ...form, regulator: e.target.value })} />
              </div>
              <div>
                <label className="label">Base currency</label>
                <select className="input" value={form.base_currency}
                  onChange={(e) => setForm({ ...form, base_currency: e.target.value })}>
                  {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
            </div>
            <div className="border-t border-border-soft pt-4">
              <div className="mb-3 flex flex-wrap items-center gap-1.5">
                <span className="text-sm font-medium text-ink">Initial admin</span>
                <span className={chip}>invite link · sets own password</span>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="label">Admin email *</label>
                  <input className="input" type="email" required value={form.admin_email}
                    onChange={(e) => setForm({ ...form, admin_email: e.target.value })} />
                </div>
                <div>
                  <label className="label">Admin name *</label>
                  <input className="input" required value={form.admin_name}
                    onChange={(e) => setForm({ ...form, admin_name: e.target.value })} />
                </div>
              </div>
            </div>
            {err && <div className="chip bg-crit-bg text-crit" role="alert">{err}</div>}
            <div className="flex flex-wrap gap-2">
              <button type="submit" className="btn-primary" disabled={busy}>
                {busy ? "Creating…" : "Create & provision firm"}
              </button>
              <button type="button" className="btn-outline" onClick={() => setShowNew(false)}>Cancel</button>
            </div>
          </form>
        </Card>
      )}

      {loading ? (
        <Spinner />
      ) : (
        <div>
          <div className="tile-label mb-3 tabular-nums">
            {firms?.length ?? 0} tenant firm{firms?.length !== 1 ? "s" : ""}
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {(firms || []).map((f) => (
              <Card key={f.id} data-testid="superadmin-firm">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 font-medium text-ink">
                      <Building2 size={15} className="text-ink-muted" />
                      {f.name}
                    </div>
                    <div className="mt-0.5 font-mono text-xs text-ink-muted">{f.slug}</div>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <span className={chip}>{f.jurisdiction} · {f.base_currency}</span>
                      <span className={`${chip} tabular-nums`}>{f.user_count} user{f.user_count !== 1 ? "s" : ""}</span>
                      {f.legal_name && <span className={chip}>{f.legal_name}</span>}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className={`chip text-[10px] ${f.is_active ? "bg-ok-bg text-ok" : "bg-border-soft text-ink-muted"}`}>
                      {f.is_active ? "Active" : "Inactive"}
                    </span>
                    <button
                      onClick={() => toggleStatus(f.id, !f.is_active)}
                      disabled={togglingId === f.id}
                      className="btn-ghost p-1.5"
                      title={f.is_active ? "Deactivate firm" : "Activate firm"}
                      aria-label={f.is_active ? "Deactivate firm" : "Activate firm"}
                    >
                      {togglingId === f.id
                        ? <RefreshCw size={13} className="animate-spin" />
                        : <Power size={13} className={f.is_active ? "text-ink-muted" : "text-ok"} />
                      }
                    </button>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
