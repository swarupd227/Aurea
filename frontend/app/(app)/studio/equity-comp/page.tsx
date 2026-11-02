"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { EquityCompView } from "@/components/views/EquityCompView";

function One() {
  return <EquityCompView household={useSearchParams().get("household")} />;
}

export default function EquityComp() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
