"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { FinancialPlanView } from "@/components/views/FinancialPlanView";

function One() {
  return <FinancialPlanView household={useSearchParams().get("household")} />;
}

export default function FinancialPlan() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
