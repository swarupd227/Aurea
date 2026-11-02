"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { CashMovementsView } from "@/components/views/CashMovementsView";

function One() {
  return <CashMovementsView household={useSearchParams().get("household")} />;
}

export default function CashMovements() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
