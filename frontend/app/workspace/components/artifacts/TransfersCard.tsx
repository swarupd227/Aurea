import { money } from "@/lib/format";
import { stageTone } from "@/components/views/TransfersView";

const pointTone: Record<string, string> = { applies: "text-warn", higher_in_ira: "text-warn", not_assessed: "text-ink-muted", laid_out: "", none: "text-ok",
  no_gain: "text-ok", lower_in_ira: "text-ok", same: "text-ok", not_yet: "text-ok", does_not_apply: "text-ok" };

export function TransfersCard({ result }: { result: any }) {
  const roll = result.rollover;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="transfers-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Transfers in</div>
        <div className="flex gap-1.5">
          <span className="chip">{result.transfers.length}</span>
          {result.late > 0 && <span className="chip text-critical">{result.late} late</span>}
        </div>
      </div>
      {result.transfers.length > 0 && (
        <table className="w-full text-xs"><tbody className="divide-y divide-border-soft">
          {result.transfers.slice(0, 6).map((t: any) => (
            <tr key={t.transfer_id}>
              <td className="px-3 py-1.5 text-ink">{t.client}</td>
              <td className="px-3 py-1.5 font-mono uppercase text-ink-muted">{t.type.replace(/_/g, " ")}</td>
              <td className="px-3 py-1.5 text-right font-mono">{t.amount != null ? money(t.amount) : "—"}</td>
              <td className="px-3 py-1.5"><span className={`chip ${stageTone[t.stage] ?? ""}`}>{t.stage.replace(/_/g, " ")}</span></td>
              <td className="px-3 py-1.5 text-ink-muted">{t.owner ?? ""}</td>
            </tr>
          ))}
        </tbody></table>
      )}
      {result.transfers.some((t: any) => t.private?.needs?.length) && (
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2" data-testid="transfers-private">
          {result.transfers.flatMap((t: any) => (t.private?.needs ?? []).map((n: string) => <span key={`${t.transfer_id}-${n}`} className="chip text-warn">{n.split(":")[0]} · {n.split(": ")[1]}</span>))}
        </div>
      )}
      {roll?.applies && (
        <div className="border-t border-border-soft px-3 py-2" data-testid="rollover-points">
          <div className="mb-1 text-[10px] uppercase tracking-wide text-ink-muted">Rollover</div>
          <div className="flex flex-wrap gap-1.5">
            {roll.points.map((p: any) => <span key={p.key} className={`chip ${pointTone[p.status] ?? ""}`}>{p.label} · {p.status.replace(/_/g, " ")}</span>)}
            <span className={`chip ${roll.adviser_reason ? "text-ok" : "text-ink-muted"}`}>Adviser's reason · {roll.adviser_reason ? "on file" : "not recorded"}</span>
          </div>
        </div>
      )}
    </div>
  );
}
