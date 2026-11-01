"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { RiskProfilesView } from "@/components/views/RiskProfilesView";

function One() {
  return <RiskProfilesView household={useSearchParams().get("household")} />;
}

export default function RiskProfiles() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
