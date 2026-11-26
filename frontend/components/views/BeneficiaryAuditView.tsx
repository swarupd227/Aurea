"use client";

import { AlertTriangle, ShieldCheck } from "lucide-react";
import { Card, StatTile, Empty, Spinner } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { titleCase } from "@/lib/format";
import { ViewHeader } from "./ViewHeader";

/** Accounts whose registration needs a beneficiary, and whether their designations add up to 100%; for an
 * Indian firm, whether each account a person holds has a nomination or a signed opt-out. With nothing checked
 * it says so, never that all is covered. Reads only. */
export function BeneficiaryAuditView() {
  const { data, loading } = useApi<any>("/api/registration/beneficiary-audit");
  const india = data?.market === "IN";
  const notChecked: any[] = data?.not_checked || [];

  return (
    <div className="view-container">
      <ViewHeader
        title={india ? "Nominations" : "Beneficiary audit"}
        meta={data ? <span className="chip text-ink-muted">{india ? "nomination or signed opt-out" : "designations must total 100%"}</span> : null}
      />

      {data && (
        <div className={`grid grid-cols-2 gap-3 mb-5 ${india ? "cq-sm:grid-cols-4" : ""}`} data-testid="beneficiary-audit-summary">
          <StatTile label={india ? "Accounts checked" : "Accounts needing beneficiaries"} value={data.accounts_checked} />
          {india && <StatTile label="Nominated" value={data.nominated} />}
          {india && <StatTile label="Opted out" value={data.opted_out} />}
          <StatTile label="Gaps found" value={data.accounts_checked ? data.gaps.length : "—"} accent={data.gaps.length ? "warn" : data.accounts_checked ? "ok" : undefined} />
        </div>
      )}

      <Card>
        <div className="font-semibold text-ink mb-3 flex items-center gap-2">
          <ShieldCheck size={15} className="text-ink-muted" /> Coverage
        </div>
        {loading && <Spinner />}
        {!loading && data?.state === "covered" && (
          <Empty icon={<ShieldCheck size={28} />}>All {data.accounts_checked} checked are covered</Empty>
        )}
        {!loading && data?.state === "nothing_checked" && (
          <div data-testid="beneficiary-audit-nothing"><Empty icon={<AlertTriangle size={28} />}>Nothing checked · {data.accounts_total} account(s) on file</Empty></div>
        )}
        {!loading && data?.gaps?.length > 0 && (
          <div className="divide-y divide-border-soft" data-testid="beneficiary-audit-gaps">
            {data.gaps.map((g: any) => (
              <div key={g.account_id} className="py-3 flex items-start gap-3">
                <AlertTriangle size={16} className="mt-0.5 text-warn shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium text-ink text-sm">{g.account_name}</span>
                    {g.holder && <span className="chip bg-border-soft text-ink-muted">{g.holder}</span>}
                    {g.registration_type !== "not recorded" && <span className="chip bg-border-soft text-ink-muted">{titleCase(g.registration_type)}</span>}
                  </div>
                  <div className="mt-1"><span className="chip bg-warn-bg text-warn">{g.issue}</span></div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {!loading && notChecked.length > 0 && (
        <Card className="mt-4">
          <div className="font-semibold text-ink mb-3">Not checked · {notChecked.length}</div>
          <div className="divide-y divide-border-soft" data-testid="beneficiary-audit-not-checked">
            {notChecked.map((n) => (
              <div key={n.account_id} className="py-2 flex items-center gap-2 flex-wrap">
                <span className="text-sm text-ink">{n.account_name}</span>
                <span className="chip bg-border-soft text-ink-muted">{n.reason}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
