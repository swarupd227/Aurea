"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { ViewHeader } from "./ViewHeader";

const th = "px-4 py-2 font-medium";
const tone: Record<string, string> = { requested: "text-warn", approved: "text-ok", declined: "text-ink-muted", removed: "text-ink-muted",
  active: "text-critical", released: "text-ink-muted", expired: "text-ink-muted" };

/** Margin, options and checkwriting asked for and decided; and temporary holds on suspected exploitation. */
export function AccountFeaturesView() {
  const { data, loading, error, refetch } = useApi<any>("/api/account-features", []);
  if (error) return <div className="view-container"><Card><ErrorState what="account features" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;
  const s = data.summary;
  return (
    <div className="view-container">
      <ViewHeader title="Account features and holds" />
      <div className="mb-4 grid grid-cols-3 gap-3" data-testid="features-summary">
        {[["Waiting for a principal", s.waiting], ["Holds in force", s.holds_active], ["Notices late", s.notices_late]].map(([label, n]) => (
          <Card key={label as string}><div className="text-[10px] uppercase tracking-wide text-ink-muted">{label}</div><div className="font-mono text-xl text-ink">{n as number}</div></Card>
        ))}
      </div>
      <Card className="mb-4 overflow-hidden p-0">
        <div className="border-b border-border-soft px-4 py-2 text-sm font-medium text-ink">Holds</div>
        {data.holds.length === 0 ? <div className="px-4 py-3"><Empty>{data.rules.senior_holds ? "No holds." : "No temporary-hold rule applies in this firm's market."}</Empty></div> : (
          <div className="overflow-x-auto"><table className="w-full text-sm" data-testid="features-holds">
            <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Client</th><th className={th}>Account</th><th className={th}>State</th><th className={th}>Placed</th><th className={th}>Ends</th><th className={th}>Notice</th><th className={th}>Trusted contacts</th></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {data.holds.map((h: any) => (
                <tr key={h.id}>
                  <td className="px-4 py-2 text-ink">{h.client}</td>
                  <td className="px-4 py-2 text-xs text-ink-muted">{h.account}</td>
                  <td className="px-4 py-2"><span className={`chip ${tone[h.state] ?? ""}`}>{h.state}{h.extensions ? ` · ${h.extensions} ext` : ""}</span></td>
                  <td className="px-4 py-2 font-mono text-xs text-ink-muted">{reviewDay(h.placed_on)}</td>
                  <td className="px-4 py-2 font-mono text-xs text-ink-muted">{reviewDay(h.expires_on)}</td>
                  <td className="px-4 py-2">{h.notified ? <span className="chip text-ok">told {reviewDay(h.notified.on)}</span>
                    : <span className={`chip ${h.notice_late ? "text-critical" : "text-warn"}`}>due {reviewDay(h.notify_by)}</span>}</td>
                  <td className="px-4 py-2 text-xs text-ink-muted">{h.trusted_contacts.join(", ") || "none on file"}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </Card>
      <Card className="overflow-hidden p-0">
        <div className="border-b border-border-soft px-4 py-2 text-sm font-medium text-ink">Features</div>
        {data.features.length === 0 ? <div className="px-4 py-3"><Empty>No features asked for.</Empty></div> : (
          <div className="overflow-x-auto"><table className="w-full text-sm" data-testid="features-rows">
            <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Client</th><th className={th}>Account</th><th className={th}>Feature</th><th className={th}>Status</th><th className={th}>Agreement</th></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {data.features.map((f: any) => (
                <tr key={f.id}>
                  <td className="px-4 py-2 text-ink">{f.client}</td>
                  <td className="px-4 py-2 text-xs text-ink-muted">{f.account}</td>
                  <td className="px-4 py-2 text-xs text-ink">{f.label}{f.level ? ` level ${f.level}` : ""}</td>
                  <td className="px-4 py-2"><span className={`chip ${tone[f.status] ?? ""}`}>{f.status}</span></td>
                  <td className="px-4 py-2 text-xs text-ink-muted">{f.agreement}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}
