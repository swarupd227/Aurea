import { money, titleCase } from "@/lib/format";

// The family aggregate (read_family) as a card: figures and chips only - the agent's message carries
// the sentences.

export interface FamilyPayload {
  anchor: { household_id: string; household: string };
  person_ids: string[];
  persons: { id: string; full_name: string; household_id: string | null; household: string | null }[];
  household_count: number;
  households: { id: string; name: string; segment: string | null }[];
  total_family_aum: number;
  aum_basis: string;
  currency: string | null;
  goal_count: number;
  goals: { name: string; kind: string; target_amount: number; household_id: string }[];
  message?: string;
}

const obj = (v: any) => v !== null && typeof v === "object" && !Array.isArray(v);

export function isFamily(r: any): boolean {
  return obj(r)
    && obj(r.anchor)
    && typeof r.anchor.household === "string"
    && typeof r.household_count === "number"
    && Array.isArray(r.households)
    && Array.isArray(r.persons)
    && typeof r.total_family_aum === "number"
    && typeof r.aum_basis === "string"
    && typeof r.goal_count === "number"
    && Array.isArray(r.goals);
}

const Chip = ({ children, tone = "text-ink-muted" }: { children: React.ReactNode; tone?: string }) => (
  <span className={`inline-block rounded border border-border px-1.5 py-0.5 font-mono text-[11px] ${tone}`}>{children}</span>
);

function Tile({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="px-3 py-2">
      <div className="font-mono text-[11px] uppercase tracking-wider text-ink-muted">{label}</div>
      <div className="mt-0.5 font-mono text-lg tabular-nums text-ink">{value}</div>
    </div>
  );
}

export function FamilyCard({ result }: { result: FamilyPayload }) {
  const r = result;
  const ccy = r.currency ?? undefined;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="family-card">
      <div className="flex items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="min-w-0 truncate text-sm font-medium text-ink">Family · {r.anchor.household}</div>
        <div className="font-mono text-xs tabular-nums text-ink-muted">{r.persons.length} people</div>
      </div>
      <div className="grid grid-cols-3 divide-x divide-border-soft border-b border-border-soft">
        <Tile label="Households" value={r.household_count} />
        <Tile label="Family AUM" value={money(r.total_family_aum, ccy)} />
        <Tile label="Goals" value={r.goal_count} />
      </div>
      <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-1.5">
        <Chip>{r.aum_basis}</Chip>
      </div>
      <table className="w-full table-fixed text-sm">
        <tbody className="divide-y divide-border-soft">
          {r.households.map((h) => (
            <tr key={h.id}>
              <td className="truncate px-3 py-1.5 text-ink">{h.name}</td>
              <td className="w-28 truncate px-3 py-1.5 text-right text-xs text-ink-muted">{h.segment ? titleCase(h.segment) : "—"}</td>
            </tr>
          ))}
          {r.goals.map((g, i) => (
            <tr key={`g${i}`}>
              <td className="truncate px-3 py-1.5 text-ink-muted">{g.name} · {titleCase(g.kind ?? "")}</td>
              <td className="w-28 px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{money(g.target_amount, ccy)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
