"use client";
import { useState } from "react";
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
];
const CURRENCIES = ["NZD", "AUD", "GBP", "USD", "SGD", "EUR"];

export default function SuperadminPage() {
  const router = useRouter();
  const user = getUser();

  // Guard
  if (typeof window !== "undefined" && user && user.role !== "superadmin") {
    router.replace("/");
    return null;
  }

  const { data: firms, loading, refetch } = useApi<any[]>("/api/superadmin/firms");
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

  async function toggleStatus(firmId: string) {
    setTogglingId(firmId);
    try {
      await api(`/api/superadmin/firms/${firmId}/status`, { method: "PATCH", body: {} });
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
    <div>
      <PageHeader
        title="Platform — Firm management"
        sub="Create and manage tenant firms. Each new firm is auto-provisioned with default segments, connectors, and agent configs."
        actions={
          <button className="btn-primary flex items-center gap-1.5" onClick={() => { setShowNew(true); setResult(null); setErr(null); }}>
            <Plus size={16} /> New firm
          </button>
        }
      />

      {result && (
        <Card className="mb-5 border-positive/30 bg-positive/5">
          <div className="flex items-start gap-3">
            <CheckCircle2 size={18} className="text-positive shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <div className="font-medium text-ink">Firm <strong>{result.name}</strong> created and provisioned.</div>
              <div className="text-sm text-ink-muted mt-1">Send this invite link to the new admin:</div>
              <div className="mt-2 flex items-center gap-2">
                <code className="flex-1 text-xs bg-navy-50 rounded px-2 py-1 truncate">{result.invite_url}</code>
                <button onClick={() => copyInvite(result.invite_url)} className="btn-ghost p-1.5 shrink-0">
                  {copied ? <Check size={13} className="text-positive" /> : <Copy size={13} />}
                </button>
              </div>
            </div>
          </div>
        </Card>
      )}

      {showNew && (
        <Card className="mb-5">
          <div className="font-semibold text-ink mb-4 flex items-center gap-2">
            <Building2 size={16} /> New firm
          </div>
          <form onSubmit={createFirm} className="space-y-4">
            <div className="grid sm:grid-cols-2 gap-4">
              <div>
                <label className="label">Firm name *</label>
                <input className="input" required value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value, slug: autoSlug(e.target.value) })} />
              </div>
              <div>
                <label className="label">Slug (auto-generated, editable)</label>
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
            <div className="border-t border-navy-100 pt-4">
              <div className="text-sm font-medium text-ink mb-3">Initial admin user</div>
              <div className="grid sm:grid-cols-2 gap-4">
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
              <p className="text-xs text-ink-muted mt-2">An invite link will be generated. The admin sets their own password.</p>
            </div>
            {err && <div className="text-sm text-critical bg-critical/5 rounded-lg px-3 py-2">{err}</div>}
            <div className="flex gap-2">
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
          <div className="text-xs font-semibold uppercase tracking-wide text-ink-muted mb-3">
            {firms?.length ?? 0} tenant firm{firms?.length !== 1 ? "s" : ""}
          </div>
          <div className="grid md:grid-cols-2 gap-3">
            {(firms || []).map((f) => (
              <Card key={f.id}>
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="font-medium text-ink flex items-center gap-2">
                      <Building2 size={15} className="text-navy-400" />
                      {f.name}
                    </div>
                    <div className="text-xs text-ink-muted mt-0.5 font-mono">{f.slug}</div>
                    <div className="flex items-center gap-3 mt-2 text-xs text-ink-muted">
                      <span>{f.jurisdiction} · {f.base_currency}</span>
                      <span>{f.user_count} user{f.user_count !== 1 ? "s" : ""}</span>
                      {f.legal_name && <span>{f.legal_name}</span>}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className={`chip text-[10px] ${f.is_active ? "bg-positive/10 text-positive" : "bg-ink-muted/10 text-ink-muted"}`}>
                      {f.is_active ? "Active" : "Inactive"}
                    </span>
                    <button
                      onClick={() => toggleStatus(f.id)}
                      disabled={togglingId === f.id}
                      className="btn-ghost p-1.5"
                      title={f.is_active ? "Deactivate firm" : "Activate firm"}
                    >
                      {togglingId === f.id
                        ? <RefreshCw size={13} className="animate-spin" />
                        : <Power size={13} className={f.is_active ? "text-ink-muted" : "text-positive"} />
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
