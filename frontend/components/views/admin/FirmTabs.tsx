"use client";

import { useState } from "react";
import { Save, Paintbrush } from "lucide-react";
import { Card, Spinner, ErrorState } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { chip } from "../ViewHeader";
import { PageOnly, Saved, q, useAsk } from "./shared";

const JURISDICTION_DEFAULTS: Record<string, { regulator: string; base_currency: string; compliance_regime: string }> = {
  NZ:    { regulator: "FMA",       base_currency: "NZD", compliance_regime: "NZ-FMA" },
  US:    { regulator: "SEC/FINRA", base_currency: "USD", compliance_regime: "US-SEC" },
  UK:    { regulator: "FCA",       base_currency: "GBP", compliance_regime: "UK-FCA" },
  IN:    { regulator: "SEBI",      base_currency: "INR", compliance_regime: "IN-SEBI" },
  MULTI: { regulator: "",          base_currency: "USD", compliance_regime: "none: not supported" },
};

/** The profile fields update_firm_profile takes, in words; null when none changed. */
function profileSentence(before: Record<string, any>, after: Record<string, any>): string | null {
  const words: [string, string, boolean][] = [
    ["name", "name", true], ["legal_name", "legal name", true], ["regulator", "regulator", false],
    ["logo_text", "logo text", true], ["tagline", "tagline", true],
    ["primary", "primary colour", false], ["accent", "accent colour", false],
  ];
  const parts: string[] = [];
  for (const [k, label, quoted] of words) {
    const a = (after[k] ?? "").toString().trim();
    const b = (before[k] ?? "").toString().trim();
    if (after[k] === undefined || a === b) continue;
    parts.push(a ? `${label} ${quoted ? q(a) : a}` : `clear the ${label}`);
  }
  return parts.length ? `Update the firm profile: ${parts.join("; ")}` : null;
}

export function FirmTab() {
  const ask = useAsk();
  const { data, loading, error, refetch } = useApi<any>("/api/admin/firm");
  const [form, setForm] = useState<any>(null);
  const [saved, setSaved] = useState(false);
  if (loading && !data) return <Spinner />;
  if (error || !data) return <Card><ErrorState what="the firm" message={error} onRetry={refetch} /></Card>;
  const f = form || {
    name: data.name, jurisdiction: data.jurisdiction, regulator: data.regulator,
    base_currency: data.base_currency || "NZD",
    branding: { ...data.branding }, settings: { ...data.settings },
  };
  const set = (patch: any) => setForm({ ...f, ...patch });
  const setBrand = (patch: any) => setForm({ ...f, branding: { ...f.branding, ...patch } });
  const legalName = f.legal_name ?? data.legal_name ?? "";

  function changeJurisdiction(jur: string) {
    const defaults = JURISDICTION_DEFAULTS[jur] || JURISDICTION_DEFAULTS.NZ;
    setForm({ ...f, jurisdiction: jur, regulator: defaults.regulator, base_currency: defaults.base_currency });
  }

  const sentence = profileSentence(
    { name: data.name, legal_name: data.legal_name, regulator: data.regulator, ...(data.branding || {}) },
    { name: f.name, legal_name: f.legal_name, regulator: f.regulator, logo_text: f.branding?.logo_text, tagline: f.branding?.tagline,
      primary: f.branding?.primary, accent: f.branding?.accent },
  );

  async function save() {
    if (ask) {
      if (sentence) ask(sentence);
      setForm(null);
      return;
    }
    await api("/api/admin/firm", { method: "PATCH", body: f });
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
    refetch();
  }

  const jurInfo = JURISDICTION_DEFAULTS[f.jurisdiction] || JURISDICTION_DEFAULTS.NZ;

  return (
    <div className="grid max-w-4xl gap-5 cq-md:grid-cols-2" data-testid="admin-firm">
      <Card>
        <div className="mb-3 font-semibold text-ink">Firm</div>
        <div className="space-y-3">
          <div><label className="label">Name</label><input className="input" value={f.name} onChange={(e) => set({ name: e.target.value })} /></div>
          <div><label className="label">Legal name</label><input className="input" value={legalName} onChange={(e) => set({ legal_name: e.target.value })} /></div>

          {ask ? (
            <div>
              <label className="label">Jurisdiction · currency</label>
              <div className="flex flex-wrap items-center gap-1.5" data-testid="admin-firm-jurisdiction">
                <span className={chip}>{data.jurisdiction}</span>
                <span className={chip}>{data.base_currency || "—"}</span>
                <PageOnly label="set on the page" />
              </div>
            </div>
          ) : (
            <div>
              <label className="label">Jurisdiction</label>
              <select className="input" value={f.jurisdiction} onChange={(e) => changeJurisdiction(e.target.value)}>
                <option value="NZ">NZ · New Zealand (FMA)</option>
                <option value="US">US · United States (SEC / FINRA)</option>
                <option value="UK">UK · United Kingdom (FCA)</option>
                <option value="IN">IN · India (SEBI)</option>
                <option value="MULTI">Multi-jurisdiction</option>
              </select>
            </div>
          )}

          <div className="flex flex-wrap gap-1.5">
            <span className={chip}>Regulator {f.regulator || "—"}</span>
            <span className={chip}>Currency {f.base_currency}</span>
            <span className={chip}>Regime {jurInfo.compliance_regime}</span>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">Regulator</label><input className="input" value={f.regulator || ""} onChange={(e) => set({ regulator: e.target.value })} /></div>
            {!ask && (
              <div><label className="label">Base currency</label>
                <select className="input" value={f.base_currency} onChange={(e) => set({ base_currency: e.target.value })}>
                  <option value="NZD">NZD · New Zealand Dollar</option>
                  <option value="USD">USD · US Dollar</option>
                  <option value="GBP">GBP · British Pound</option>
                  <option value="INR">INR · Indian Rupee</option>
                  <option value="AUD">AUD · Australian Dollar</option>
                  <option value="EUR">EUR · Euro</option>
                </select>
              </div>
            )}
          </div>

          <div><label className="label">AI usage policy</label>
            {ask ? (
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="line-clamp-2 min-w-0 flex-1 text-sm text-ink-soft" title={f.settings?.ai_usage_policy || ""}>{f.settings?.ai_usage_policy || "—"}</span>
                <PageOnly label="set on the page" />
              </div>
            ) : (
              <textarea className="input" rows={3} value={f.settings?.ai_usage_policy || ""} onChange={(e) => setForm({ ...f, settings: { ...f.settings, ai_usage_policy: e.target.value } })} />
            )}
          </div>
        </div>
      </Card>
      <Card>
        <div className="mb-3 font-semibold text-ink">Branding</div>
        <div className="space-y-3">
          <div><label className="label">Logo text</label><input className="input" value={f.branding?.logo_text || ""} onChange={(e) => setBrand({ logo_text: e.target.value })} /></div>
          <div><label className="label">Tagline</label><input className="input" value={f.branding?.tagline || ""} onChange={(e) => setBrand({ tagline: e.target.value })} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">Primary</label><input className="input" type="color" value={f.branding?.primary || "#163a52"} onChange={(e) => setBrand({ primary: e.target.value })} /></div>
            <div><label className="label">Accent</label><input className="input" type="color" value={f.branding?.accent || "#c8a35e"} onChange={(e) => setBrand({ accent: e.target.value })} /></div>
          </div>
        </div>
      </Card>
      <div className="flex flex-wrap items-center gap-3 cq-md:col-span-2">
        <button className="btn-primary" onClick={save} disabled={!!ask && !sentence} data-testid="admin-firm-save">
          <Save size={16} /> {ask ? "Ask Astra" : "Save changes"}
        </button>
        {form && ask && <button className="btn-ghost" onClick={() => setForm(null)}>Reset</button>}
        {saved && <Saved />}
      </div>
    </div>
  );
}

export function BrandingTab() {
  const ask = useAsk();
  const { data, loading, error, refetch } = useApi<any>("/api/admin/firm/branding");
  const [form, setForm] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  if (loading && !data) return <Spinner />;
  if (error && !data) return <Card><ErrorState what="the branding" message={error} onRetry={refetch} /></Card>;
  const b = form || data?.branding || {};
  const sentence = form ? profileSentence(data?.branding || {}, {
    logo_text: form.logo_text, tagline: form.tagline, primary: form.primary, accent: form.accent,
  }) : null;

  async function save() {
    if (ask) {
      if (sentence) ask(sentence);
      setForm(null);
      return;
    }
    setSaving(true);
    try {
      await api("/api/admin/firm/branding", { method: "PATCH", body: form || {} });
      setSaved(true);
      refetch();
      setTimeout(() => setSaved(false), 2500);
    } catch {}
    setSaving(false);
  }

  function set(key: string, val: string) {
    setForm((f: any) => ({ ...(f || b), [key]: val }));
  }

  const preview = form || b;
  const primary = preview.primary || "#163a52";
  const accent = preview.accent || "#c8a35e";

  return (
    <div className="grid gap-5 cq-md:grid-cols-2" data-testid="admin-branding">
      <Card>
        <div className="mb-4 flex items-center gap-2 font-semibold text-ink"><Paintbrush size={16} /> Brand</div>
        <div className="space-y-4">
          <div>
            <label className="label">Logo text</label>
            <input className="input" value={preview.logo_text || ""} onChange={(e) => set("logo_text", e.target.value)} placeholder="Astra for Wealth" />
          </div>
          <div>
            <label className="label">Tagline</label>
            <input className="input" value={preview.tagline || ""} onChange={(e) => set("tagline", e.target.value)} />
          </div>
          <div>
            <label className="label">Logo URL</label>
            {ask ? (
              <div className="flex flex-wrap items-center gap-1.5" data-testid="admin-branding-logo-url">
                <span className="min-w-0 truncate font-mono text-xs text-ink-soft">{preview.logo_url || "—"}</span>
                <PageOnly label="set on the page" />
              </div>
            ) : (
              <input className="input font-mono text-xs" value={preview.logo_url || ""} onChange={(e) => set("logo_url", e.target.value)} placeholder="https://" />
            )}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Primary colour</label>
              <div className="flex items-center gap-2">
                <input type="color" className="h-9 w-12 cursor-pointer rounded border border-border bg-surface" value={primary} onChange={(e) => set("primary", e.target.value)} aria-label="Primary colour" />
                <input className="input min-w-0 flex-1 font-mono text-xs" value={primary} onChange={(e) => set("primary", e.target.value)} />
              </div>
            </div>
            <div>
              <label className="label">Accent colour</label>
              <div className="flex items-center gap-2">
                <input type="color" className="h-9 w-12 cursor-pointer rounded border border-border bg-surface" value={accent} onChange={(e) => set("accent", e.target.value)} aria-label="Accent colour" />
                <input className="input min-w-0 flex-1 font-mono text-xs" value={accent} onChange={(e) => set("accent", e.target.value)} />
              </div>
            </div>
          </div>
        </div>
        <div className="mt-5 flex flex-wrap items-center gap-2">
          <button className="btn-primary" onClick={save} disabled={saving || !form || (!!ask && !sentence)} data-testid="admin-branding-save">
            {saving ? "Saving…" : <><Save size={14} /> {ask ? "Ask Astra" : "Save branding"}</>}
          </button>
          {form && <button className="btn-outline" onClick={() => setForm(null)}>Reset</button>}
          {saved && <Saved />}
        </div>
      </Card>

      <Card>
        <div className="tile-label mb-3">Canvas header</div>
        <div className="rounded-xl p-5" style={{ background: primary, color: "#fff" }} data-testid="admin-branding-preview">
          <div className="text-sm opacity-60">{preview.logo_text || "Your Firm"}</div>
          <div className="mt-1 font-serif text-xl">Client</div>
          {preview.tagline && <div className="mt-1 text-sm opacity-70">{preview.tagline}</div>}
          <div className="mt-3 inline-block rounded-lg px-3 py-1.5 text-sm font-medium" style={{ background: accent, color: "#fff" }}>
            Wealth
          </div>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <div className="h-10 w-10 rounded-xl" style={{ background: accent }} />
          <div className="font-medium" style={{ color: accent }}>{preview.logo_text || "Your Firm"}</div>
          <span className={`${chip} font-mono`}>{accent}</span>
        </div>
      </Card>
    </div>
  );
}
