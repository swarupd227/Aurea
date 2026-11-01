"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { NetWorthView } from "@/components/views/NetWorthView";

function One() {
  return <NetWorthView household={useSearchParams().get("household")} />;
}

export default function NetWorth() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
