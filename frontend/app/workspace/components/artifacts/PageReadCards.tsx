import { money, pct, timeAgo, titleCase } from "@/lib/format";

// Cards for the reads that stand in for pages: Agent history, Capacity, Analytics, Onboarding and
// Provenance. Figures, tables and chips only - the agent's message carries the sentences. Each is
// handed a payload the ArtifactRenderer has already shape-checked.

const Chip = ({ children, tone = "text-ink-muted" }: { children: React.ReactNode; tone?: string }) => (
  <span className={`inline-block rounded border border-border px-1.5 py-0.5 font-mono text-[11px] ${tone}`}>{children}</span>
);

function Frame({ title, count, testId, children }: { title: string; count?: React.ReactNode; testId: string; children: React.ReactNode }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid={testId}>
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{title}</div>
        {count !== undefined && <div className="font-mono text-xs tabular-nums text-ink-muted">{count}</div>}
      </div>
      {children}
    </div>
  );
}

function Tile({ label, value, hint }: { label: string; value: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div className="px-3 py-2">
      <div className="font-mono text-[11px] uppercase tracking-wider text-ink-muted">{label}</div>
      <div className="mt-0.5 font-mono text-lg tabular-nums text-ink">{value}</div>
      {hint && <div className="mt-0.5">{hint}</div>}
    </div>
  );
}

const STATUS_TONE: Record<string, string> = {
  completed: "text-ok", failed: "text-crit", awaiting_approval: "text-warn", paused: "text-warn",
};
const tier = (t: string) => t.replace("tier_", "Tier ");

// ── Agent history ───────────────────────────────────────────────────────────────────────────
export interface AgentHistoryPayload {
  shown: number;
  by_status: Record<string, number>;
  runs: { id: string; agent_name: string; status: string; tier: string; trigger: string; created_at: string | null }[];
}

export function AgentHistoryCard({ result }: { result: AgentHistoryPayload }) {
  return (
    <Frame title="Agent runs" count={result.shown} testId="agent-history-card">
      <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-1.5">
        {Object.entries(result.by_status).map(([k, n]) => (
          <Chip key={k} tone={STATUS_TONE[k]}>{titleCase(k)} {n}</Chip>
        ))}
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          {result.runs.map((r) => (
            <tr key={r.id}>
              <td className="px-3 py-1.5 text-ink">{r.agent_name}</td>
              <td className={`px-3 py-1.5 text-xs ${STATUS_TONE[r.status] ?? "text-ink-muted"}`}>{titleCase(r.status)}</td>
              <td className="px-3 py-1.5 font-mono text-xs text-ink-muted">{tier(String(r.tier))}</td>
              <td className="px-3 py-1.5 text-xs text-ink-muted">{r.trigger}</td>
              <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted whitespace-nowrap">{timeAgo(r.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Frame>
  );
}

// ── Tasks ───────────────────────────────────────────────────────────────────────────────────
export interface TasksPayload {
  shown: number;
  total: number;
  overdue: number;
  unassigned: number;
  tasks: { id: string; title: string; status: string; due_date: string | null; overdue: boolean; subject_label: string | null; assigned_to_name: string | null }[];
}

export function TasksCard({ result }: { result: TasksPayload }) {
  return (
    <Frame title="Tasks" count={result.total > result.shown ? `${result.shown} of ${result.total}` : result.total} testId="tasks-card">
      {(result.overdue > 0 || result.unassigned > 0) && (
        <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-1.5">
          {result.overdue > 0 && <Chip tone="text-warn">Overdue {result.overdue}</Chip>}
          {result.unassigned > 0 && <Chip>Unassigned {result.unassigned}</Chip>}
        </div>
      )}
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          {result.tasks.map((t) => (
            <tr key={t.id}>
              <td className={`px-3 py-1.5 ${t.status === "open" ? "text-ink" : "text-ink-muted line-through"}`}>{t.title}</td>
              <td className="px-3 py-1.5 text-xs text-ink-muted">{t.subject_label ?? "—"}</td>
              <td className="px-3 py-1.5 text-xs text-ink-muted">{t.assigned_to_name ?? "—"}</td>
              <td className={`px-3 py-1.5 text-right font-mono text-xs tabular-nums whitespace-nowrap ${t.overdue ? "text-warn" : "text-ink-muted"}`}>{t.due_date ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Frame>
  );
}

// ── Capacity ────────────────────────────────────────────────────────────────────────────────
export interface CapacityPayload {
  total_agent_runs: number;
  decisions_made: number;
  open_items: number;
  estimated_hours_reclaimed: number;
  recommendations_by_status: Record<string, number>;
  estimate: { minutes_per_item: number; measured: boolean };
}

export function CapacityCard({ result }: { result: CapacityPayload }) {
  return (
    <Frame title="Capacity and outcomes" testId="capacity-card">
      <div className="grid grid-cols-2 divide-x divide-y divide-border-soft sm:grid-cols-4 sm:divide-y-0">
        <Tile label="Agent runs" value={result.total_agent_runs} />
        <Tile label="Decisions" value={result.decisions_made} />
        <Tile label="Open" value={result.open_items} />
        <Tile
          label="Hours reclaimed"
          value={result.estimated_hours_reclaimed}
          hint={<Chip tone="text-warn">{result.estimate.measured ? "measured" : `estimate · ${result.estimate.minutes_per_item} min each`}</Chip>}
        />
      </div>
      <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-1.5">
        {Object.entries(result.recommendations_by_status).filter(([, n]) => n > 0).map(([k, n]) => (
          <Chip key={k}>{titleCase(k)} {n}</Chip>
        ))}
      </div>
    </Frame>
  );
}

// ── Analytics ───────────────────────────────────────────────────────────────────────────────
export interface AnalyticsPayload {
  currency: string;
  headline: Record<string, number | boolean | null>;
  bases: Record<string, string>;
}

const FIGURES: { key: string; label: string; kind: "money" | "pct" | "num" | "ok" }[] = [
  { key: "firm_aum", label: "Assets under management", kind: "money" },
  { key: "clients", label: "Households", kind: "num" },
  { key: "firm_wallet_share", label: "Wallet share", kind: "pct" },
  { key: "consolidation_opportunity", label: "Consolidation opportunity", kind: "money" },
  { key: "total_return", label: "Total return", kind: "pct" },
  { key: "drift_breaches", label: "Mandates outside tolerance", kind: "num" },
  { key: "goals_on_track_pct", label: "Goals on track", kind: "pct" },
  { key: "attrition_high", label: "High attrition risk", kind: "num" },
  { key: "fee_revenue", label: "Fee revenue", kind: "money" },
  { key: "firm_margin", label: "Margin", kind: "pct" },
  { key: "data_quality_score", label: "Data quality", kind: "pct" },
  { key: "ledger_valid", label: "Decision ledger", kind: "ok" },
];

export function AnalyticsCard({ result }: { result: AnalyticsPayload }) {
  const h = result.headline;
  const show = (kind: string, v: any) => {
    if (v === null || v === undefined) return "—";
    if (kind === "money") return money(v, result.currency);
    if (kind === "pct") return pct(v);
    if (kind === "ok") return v ? <span className="text-ok">verifies</span> : <span className="text-crit">broken</span>;
    return String(v);
  };
  return (
    <Frame title="Analytics" count={result.currency} testId="analytics-card">
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          {FIGURES.filter((f) => f.key in h).map((f) => (
            <tr key={f.key}>
              <td className="px-3 py-1.5 text-ink">{f.label}</td>
              <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink">{show(f.kind, h[f.key])}</td>
              <td className="px-3 py-1.5 text-right">
                {result.bases[f.key] && <Chip tone="text-warn">{result.bases[f.key].split(" ")[0]}</Chip>}
              </td>
            </tr>
          ))}
          {typeof h.agents_total === "number" && (
            <tr>
              <td className="px-3 py-1.5 text-ink">Agents healthy</td>
              <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink">{String(h.agents_healthy)} / {String(h.agents_total)}</td>
              <td />
            </tr>
          )}
        </tbody>
      </table>
    </Frame>
  );
}

// ── Onboarding ──────────────────────────────────────────────────────────────────────────────
export interface OnboardingPayload {
  summary: { total: number; open: number; sla_breached: number; blocked: number; ready_to_activate: number };
  cases: {
    id: string; prospect_name: string; status: string; sla_status: string; nigo_flag: boolean;
    aml_risk_tier: string | null; position: string; owner: string | null; ready_to_activate: boolean;
    tracks: { code: string; label: string; state: string }[];
  }[];
}

const TRACK_TONE: Record<string, string> = {
  complete: "text-ok", blocked: "text-crit", waiting_external: "text-warn", in_progress: "text-ink", not_started: "text-ink-muted",
};

export function OnboardingCasesCard({ result }: { result: OnboardingPayload }) {
  const s = result.summary;
  return (
    <Frame title="Onboarding" count={`${s.open} open of ${s.total}`} testId="onboarding-card">
      <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-1.5">
        <Chip tone={s.blocked ? "text-crit" : undefined}>Blocked {s.blocked}</Chip>
        <Chip tone={s.sla_breached ? "text-warn" : undefined}>Past SLA {s.sla_breached}</Chip>
        <Chip tone={s.ready_to_activate ? "text-ok" : undefined}>Ready {s.ready_to_activate}</Chip>
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          {result.cases.map((c) => (
            <tr key={c.id}>
              <td className="px-3 py-1.5">
                <div className="text-ink">{c.prospect_name}</div>
                <div className="text-xs text-ink-muted">{c.position}{c.owner ? ` · ${titleCase(c.owner)}` : ""}</div>
              </td>
              <td className="px-3 py-1.5">
                <div className="flex gap-1">
                  {c.tracks.map((t) => (
                    <span key={t.code} title={`${t.label}: ${titleCase(t.state)}`}
                      className={`rounded border border-border px-1.5 py-0.5 font-mono text-[11px] ${TRACK_TONE[t.state] ?? "text-ink-muted"}`}>
                      {t.code}
                    </span>
                  ))}
                </div>
              </td>
              <td className="px-3 py-1.5 text-right text-xs whitespace-nowrap">
                {c.nigo_flag && <Chip tone="text-crit">NIGO</Chip>}{" "}
                {c.sla_status === "breached" || c.sla_status === "at_risk" ? (
                  <Chip tone={c.sla_status === "breached" ? "text-crit" : "text-warn"}>{titleCase(c.sla_status)}</Chip>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Frame>
  );
}

// ── Provenance ──────────────────────────────────────────────────────────────────────────────
export interface ProvenancePayload {
  ledger: { valid: boolean; count: number; broken_at_seq: number | null };
  surveillance: {
    open: number; by_severity: Record<string, number>;
    flags: { id: string; severity: string; category: string; target_agent_name: string | null; auto_paused_agent: boolean; escalated: boolean }[];
  };
  evaluations: { agent_key: string; agent_name: string; quality_score: number; grade: string; sample_size: number }[];
  autonomy_changes: { agent_name: string; from_tier: string; to_tier: string; paused: boolean; automatic: boolean; created_at: string | null }[];
}

const SEVERITY_TONE: Record<string, string> = { high: "text-crit", medium: "text-warn", low: "text-ink-muted", info: "text-ink-muted" };
const GRADE_TONE: Record<string, string> = { healthy: "text-ok", watch: "text-warn", regressed: "text-crit" };

export function ProvenanceCard({ result }: { result: ProvenancePayload }) {
  const { ledger, surveillance } = result;
  return (
    <Frame title="Provenance" testId="provenance-card">
      <div className="grid grid-cols-2 divide-x divide-border-soft">
        <Tile
          label="Decision ledger"
          value={ledger.count}
          hint={ledger.valid ? <Chip tone="text-ok">chain intact</Chip> : <Chip tone="text-crit">broken at {ledger.broken_at_seq}</Chip>}
        />
        <Tile
          label="Open flags"
          value={surveillance.open}
          hint={
            <span className="flex flex-wrap gap-1">
              {Object.entries(surveillance.by_severity).map(([k, n]) => (
                <Chip key={k} tone={SEVERITY_TONE[k]}>{k} {n}</Chip>
              ))}
            </span>
          }
        />
      </div>
      {surveillance.flags.length > 0 && (
        <table className="w-full border-t border-border-soft text-sm">
          <tbody className="divide-y divide-border-soft">
            {surveillance.flags.map((f) => (
              <tr key={f.id}>
                <td className={`px-3 py-1.5 font-mono text-xs ${SEVERITY_TONE[f.severity] ?? "text-ink-muted"}`}>{f.severity}</td>
                <td className="px-3 py-1.5 text-ink">{titleCase(f.category)}</td>
                <td className="px-3 py-1.5 text-xs text-ink-muted">{f.target_agent_name}</td>
                <td className="px-3 py-1.5 text-right text-xs whitespace-nowrap">
                  {f.auto_paused_agent && <Chip tone="text-warn">paused agent</Chip>} {f.escalated && <Chip>escalated</Chip>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {result.evaluations.length > 0 && (
        <table className="w-full border-t border-border-soft text-sm">
          <tbody className="divide-y divide-border-soft">
            {result.evaluations.map((e) => (
              <tr key={e.agent_key}>
                <td className="px-3 py-1.5 text-ink">{e.agent_name}</td>
                <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{Math.round(e.quality_score * 100)}%</td>
                <td className={`px-3 py-1.5 text-right text-xs ${GRADE_TONE[e.grade] ?? "text-ink-muted"}`}>{e.grade}</td>
                <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">n={e.sample_size}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {result.autonomy_changes.length > 0 && (
        <table className="w-full border-t border-border-soft text-sm">
          <tbody className="divide-y divide-border-soft">
            {result.autonomy_changes.map((c, i) => (
              <tr key={i}>
                <td className="px-3 py-1.5 text-ink">{c.agent_name}</td>
                <td className="px-3 py-1.5 font-mono text-xs text-ink-muted">{tier(c.from_tier)} → {tier(c.to_tier)}</td>
                <td className="px-3 py-1.5 text-right text-xs whitespace-nowrap">
                  {c.paused && <Chip tone="text-warn">paused</Chip>} <Chip>{c.automatic ? "automatic" : "manual"}</Chip>
                </td>
                <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted whitespace-nowrap">{timeAgo(c.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Frame>
  );
}
