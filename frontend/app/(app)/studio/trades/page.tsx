"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { TradesView } from "@/components/views/TradesView";

function One() {
  return <TradesView household={useSearchParams().get("household")} />;
}

export default function Trades() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
