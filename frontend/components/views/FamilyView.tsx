"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { money, titleCase } from "@/lib/format";
import { ViewLink } from "./ViewNav";
import { AllClients, ViewHeader } from "./ViewHeader";

const ROUTE = "/studio/family";

/** A family across every household it is linked to: its households, people, managed wealth and goals.
 * With no household, a picker of every client. */
export function FamilyView({ household = null }: { household?: string | null }) {
  return household ? <One id={household} /> : <Picker />;
}

function Picker() {
  const { data, loading, error, refetch } = useApi<any[]>("/api/core/households");
  if (error) return <div className="view-container"><Card><ErrorState what="households" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;
  return (
    <div className="view-container">
      <ViewHeader title="Family wealth" meta={<span className="chip text-ink-muted">{data.length} households</span>} />
      {data.length === 0 ? <Empty>No households.</Empty> : (
        <Card className="overflow-hidden p-0">
          <ul className="divide-y divide-border-soft" data-testid="family-households-picker">
            {data.map((h: any) => (
              <li key={h.id}>
                <ViewLink href={`${ROUTE}?household=${h.id}`} data-testid="family-household-pick"
                  className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 hover:bg-border-soft">
                  <span className="min-w-0 flex-1 truncate text-sm text-ink">{h.name}</span>
                  {h.segment && <span className="chip text-ink-muted">{titleCase(h.segment)}</span>}
                  <span className="font-mono text-sm tabular-nums text-ink-muted">{money(h.total_value)}</span>
                </ViewLink>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function One({ id }: { id: string }) {
  const path = `/api/studio/family?household_id=${encodeURIComponent(id)}`;
  const { data, loading, error, refetch } = useApi<any>(path, [path]);
  if (error) return <div className="view-container"><Card><ErrorState what="the family" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;

  const ccy: string | undefined = data.currency ?? undefined;
  const households: any[] = data.households ?? [];
  const persons: any[] = data.persons ?? [];
  const goals: any[] = data.goals ?? [];
  const nameOf = (hid: string | null) => households.find((h) => h.id === hid)?.name ?? null;
  const anchorId: string = data.anchor?.household_id ?? id;

  return (
    <div className="view-container">
      <ViewHeader
        title={`Family · ${data.anchor?.household ?? "Household"}`}
        meta={data.currency ? <span className="chip text-ink-muted">{data.currency}</span> : undefined}
        actions={<AllClients href={ROUTE} />}
      />

      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="family-summary">
        <Card>
          <div className="tile-label">Households</div>
          <div className="font-mono text-xl tabular-nums text-ink">{data.household_count}</div>
        </Card>
        <Card>
          <div className="tile-label">People</div>
          <div className="font-mono text-xl tabular-nums text-ink">{persons.length}</div>
        </Card>
        <Card>
          <div className="tile-label">Family AUM</div>
          <div className="font-mono text-xl tabular-nums text-ink">{money(data.total_family_aum, ccy)}</div>
          {data.aum_basis && <div className="mt-1"><span className="chip text-ink-muted" data-testid="family-aum-basis">{data.aum_basis}</span></div>}
        </Card>
        <Card>
          <div className="tile-label">Goals</div>
          <div className="font-mono text-xl tabular-nums text-ink">{data.goal_count}</div>
        </Card>
      </div>

      <h2 className="mb-2 text-sm font-medium text-ink">Households</h2>
      {households.length === 0 ? <Empty>No linked households.</Empty> : (
        <Card className="mb-4 overflow-hidden p-0">
          <ul className="divide-y divide-border-soft" data-testid="family-households">
            {households.map((h: any) => (
              <li key={h.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2">
                <ViewLink href={`/studio/clients/${h.id}`} className="min-w-0 flex-1 truncate text-sm text-ink hover:underline" data-testid="family-client">{h.name}</ViewLink>
                {h.id === anchorId && <span className="chip text-ink-muted">this household</span>}
                {h.segment && <span className="chip text-ink-muted">{titleCase(h.segment)}</span>}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <h2 className="mb-2 text-sm font-medium text-ink">People</h2>
      {persons.length === 0 ? <Empty>No people on file.</Empty> : (
        <Card className="mb-4 overflow-hidden p-0">
          <ul className="divide-y divide-border-soft" data-testid="family-people">
            {persons.map((p: any) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2">
                <span className="min-w-0 flex-1 truncate text-sm text-ink">{p.full_name}</span>
                <span className="text-xs text-ink-muted">{p.household ?? "no household"}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <h2 className="mb-2 text-sm font-medium text-ink">Goals</h2>
      {goals.length === 0 ? <Empty>No goals on file.</Empty> : (
        <Card className="overflow-hidden p-0">
          <ul className="divide-y divide-border-soft" data-testid="family-goals">
            {goals.map((g: any, i: number) => (
              <li key={i} className="flex items-start justify-between gap-2 px-4 py-2">
                <div className="min-w-0">
                  <div className="truncate text-sm text-ink">{g.name}</div>
                  <div className="text-xs text-ink-muted">{titleCase(g.kind ?? "")}{nameOf(g.household_id) ? ` · ${nameOf(g.household_id)}` : ""}</div>
                </div>
                <span className="shrink-0 font-mono text-sm tabular-nums text-ink">{money(g.target_amount, ccy)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
