"use client";

import { AlertTriangle, ShieldCheck } from "lucide-react";
import { Card, StatTile, Empty, Spinner } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { titleCase } from "@/lib/format";
import { ViewHeader } from "./ViewHeader";

/** Accounts whose registration needs a beneficiary, and whether their designations add up to 100%.
 * Reads only. */
export function BeneficiaryAuditView() {
  const { data, loading } = useApi<any>("/api/registration/beneficiary-audit");

  return (
    <div className="view-container">
      <ViewHeader
        title="Beneficiary audit"
        meta={<span className="chip text-ink-muted">designations must total 100%</span>}
      />

      {data && (
        <div className="grid grid-cols-2 gap-3 mb-5" data-testid="beneficiary-audit-summary">
          <StatTile label="Accounts needing beneficiaries" value={data.accounts_checked} />
          <StatTile label="Gaps found" value={data.gaps.length} accent={data.gaps.length ? "warn" : "ok"} />
        </div>
      )}

      <Card>
        <div className="font-semibold text-ink mb-3 flex items-center gap-2">
          <ShieldCheck size={15} className="text-ink-muted" /> Coverage
        </div>
        {loading && <Spinner />}
        {!loading && data?.gaps?.length === 0 && (
          <Empty icon={<ShieldCheck size={28} />}>Every account is covered.</Empty>
        )}
        {!loading && data?.gaps?.length > 0 && (
          <div className="divide-y divide-border-soft" data-testid="beneficiary-audit-gaps">
            {data.gaps.map((g: any) => (
              <div key={g.account_id} className="py-3 flex items-start gap-3">
                <AlertTriangle size={16} className="mt-0.5 text-warn shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium text-ink text-sm">{g.account_name}</span>
                    <span className="chip bg-border-soft text-ink-muted">{titleCase(g.registration_type)}</span>
                  </div>
                  <div className="mt-1"><span className="chip bg-warn-bg text-warn">{g.issue}</span></div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
