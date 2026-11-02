import { money, moneyShort, pct } from "@/lib/format";

// The book screened a row per household (`screen_book`, backend aurea_core/book_screen.py). Figures and
// chips only: which households, what was found, and nothing that reads as advice.

interface EquityRow {
  mandate: string;
  equity_weight: number | null;
  equity_target: number | null;
  over_by: number | null;
  over_target: boolean;
  measured_against: "ips" | "model" | null;
}

interface ScreenRow {
  household_id: string;
  household: string;
  total_value: number;
  equity: EquityRow[];
  over_equity_target: boolean;
  unrealised_loss: number;
  positions_at_loss: number;
  values: { exclusions: string[]; not_on_mandates: { mandate: string; not_carried: string[] }[]; held_against_value: number };
  values_gap: boolean;
  next_gen: string[];
}

export interface BookScreenPayload {
  currency: string | null;
  filters: string[];
  households_screened: number;
  matched: number;
  households: ScreenRow[];
}

const FILTER_LABEL: Record<string, string> = {
  over_equity_target: "over equity target", unrealised_loss: "unrealised loss", values_gap: "values gap", next_gen: "next gen",
};

const Chip = ({ children, tone = "text-ink-muted" }: { children: React.ReactNode; tone?: string }) => (
  <span className={`inline-block rounded border border-border px-1.5 py-0.5 font-mono text-[11px] ${tone}`}>{children}</span>
);

function worstEquity(r: ScreenRow): EquityRow | undefined {
  return r.equity.filter((e) => e.over_by !== null).sort((a, b) => (b.over_by ?? 0) - (a.over_by ?? 0))[0];
}

export function BookScreenCard({ result }: { result: BookScreenPayload }) {
  const ccy = result.currency || undefined;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="book-screen-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-sm font-medium text-ink">Book screen</span>
          {result.filters.map((f) => <Chip key={f}>{FILTER_LABEL[f] ?? f}</Chip>)}
        </div>
        <div className="font-mono text-xs tabular-nums text-ink-muted">
          {result.matched} of {result.households_screened}
        </div>
      </div>
      {result.households.length === 0 ? (
        <div className="px-3 py-3 font-mono text-xs text-ink-muted">0 households</div>
      ) : (
        <ul className="divide-y divide-border-soft">
          {result.households.map((r) => {
            const eq = worstEquity(r);
            return (
              <li key={r.household_id} className="px-3 py-1.5">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="min-w-0 truncate text-sm text-ink">{r.household}</span>
                  <span className="shrink-0 font-mono text-xs tabular-nums text-ink-muted">{money(r.total_value, ccy)}</span>
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {eq ? (
                    <Chip tone={r.over_equity_target ? "text-warn" : "text-ink-muted"}>
                      equity {pct(eq.equity_weight, 0)} / {pct(eq.equity_target, 0)} {eq.measured_against}
                    </Chip>
                  ) : (
                    <Chip>equity not measured</Chip>
                  )}
                  {r.positions_at_loss > 0 && (
                    <Chip tone="text-warn">loss {moneyShort(r.unrealised_loss, ccy)} · {r.positions_at_loss}</Chip>
                  )}
                  {r.values.not_on_mandates.length > 0 && (
                    <Chip tone="text-warn">
                      not on mandate: {Array.from(new Set(r.values.not_on_mandates.flatMap((m) => m.not_carried))).join(", ")}
                    </Chip>
                  )}
                  {r.values.held_against_value > 0 && (
                    <Chip tone="text-crit">held against values {moneyShort(r.values.held_against_value, ccy)}</Chip>
                  )}
                  {r.next_gen.length > 0 && <Chip>next gen · {r.next_gen.length}</Chip>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
