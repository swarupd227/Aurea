"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ConsolidationView } from "@/components/views/ConsolidationView";

function One() {
  return <ConsolidationView household={useSearchParams().get("household")} />;
}

export default function Consolidation() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
