"use client";
import { AlertTriangle, ShieldCheck } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, StatTile, Empty, Spinner } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { titleCase } from "@/lib/format";

export default function BeneficiaryAudit() {
  const { data, loading } = useApi<any>("/api/registration/beneficiary-audit");

  return (
    <div>
      <PageHeader title="Beneficiary audit"
        sub="Every account whose registration type needs a beneficiary designation, and whether it has one that adds up to 100% (L200-1 §4) — the periodic confirmation campaign, always current." />

      {data && (
        <div className="grid grid-cols-2 gap-4 mb-6">
          <StatTile label="Accounts needing beneficiaries" value={data.accounts_checked} />
          <StatTile label="Gaps found" value={data.gaps.length} accent={data.gaps.length ? "warn" : "ok"} />
        </div>
      )}

      <Card>
        <div className="font-semibold text-ink mb-3 flex items-center gap-2">
          <ShieldCheck size={17} /> Coverage
        </div>
        {loading && <Spinner />}
        {!loading && data?.gaps?.length === 0 && (
          <Empty icon={<ShieldCheck size={28} />}>Every account needing a beneficiary designation is covered.</Empty>
        )}
        {!loading && data?.gaps?.length > 0 && (
          <div className="divide-y divide-border-soft">
            {data.gaps.map((g: any) => (
              <div key={g.account_id} className="py-3 flex items-start gap-3">
                <AlertTriangle size={16} className="mt-0.5 text-warn shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium text-ink text-sm">{g.account_name}</span>
                    <span className="chip bg-border-soft text-ink-muted">{titleCase(g.registration_type)}</span>
                  </div>
                  <div className="text-xs text-warn mt-0.5">{g.issue}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
