"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { KycRegistryView } from "@/components/views/KycRegistryView";

function One() {
  return <KycRegistryView household={useSearchParams().get("household")} />;
}

export default function KycRegistry() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
