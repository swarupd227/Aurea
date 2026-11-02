"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { RegulatoryCountdownView } from "@/components/views/RegulatoryCountdownView";

function One() {
  return <RegulatoryCountdownView household={useSearchParams().get("household")} />;
}

export default function RegulatoryCountdown() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
