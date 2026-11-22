// A goal's tier and the household's tiers (B-176). A household's success is every needs goal met together; a goal
// with no tier on file is "not tiered", never read as a need. Chips and figures only.

export type Tier = "needs" | "wants" | "wishes";

export interface Tiers {
  by_tier: Partial<Record<Tier, { goals: number; all_met: number; on_track: boolean; short: string[] }>>;
  not_tiered: string[];
  success: number | null;
  success_basis: string;
  failed_tier: Tier | null;
}

const LABEL: Record<Tier, string> = { needs: "Need", wants: "Want", wishes: "Wish" };
const PLURAL: Record<Tier, string> = { needs: "Needs", wants: "Wants", wishes: "Wishes" };

/** One goal's tier, or "not tiered". */
export function TierChip({ tier }: { tier?: string | null }) {
  const t = tier as Tier | undefined;
  return t && LABEL[t]
    ? <span className="chip bg-border-soft text-ink-soft" data-testid="goal-tier">{LABEL[t]}</span>
    : <span className="chip bg-border-soft text-ink-muted" data-testid="goal-tier">not tiered</span>;
}

/** The household's tiers: how often each tier's goals are all met together, the one that falls short, what is not tiered. */
export function TiersRow({ tiers }: { tiers?: Tiers | null }) {
  if (!tiers) return null;
  const tiered = (Object.keys(tiers.by_tier) as Tier[]).filter((t) => tiers.by_tier[t]);
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="goal-tiers">
      {tiered.length === 0 && <span className="chip bg-border-soft text-ink-muted">no goal tiered as a need, want or wish</span>}
      {(["needs", "wants", "wishes"] as Tier[]).filter((t) => tiers.by_tier[t]).map((t) => {
        const row = tiers.by_tier[t]!;
        return (
          <span key={t} className={`chip tabular-nums ${row.on_track ? "bg-ok-bg text-ok" : "bg-warn-bg text-warn"}`}
            title={row.short.length ? `Short: ${row.short.join(", ")}` : undefined} data-testid={`goal-tier-${t}`}>
            {PLURAL[t]} all met {Math.round(row.all_met * 100)}% · {row.goals}
          </span>
        );
      })}
      {tiers.success == null && tiered.length > 0 && <span className="chip bg-border-soft text-ink-muted">no needs tier: no household success figure</span>}
      {tiers.not_tiered.length > 0 && (
        <span className="chip bg-border-soft text-ink-muted" title={tiers.not_tiered.join(", ")} data-testid="goal-tiers-untiered">
          {tiers.not_tiered.length} not tiered
        </span>
      )}
    </div>
  );
}
