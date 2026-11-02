"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { TaxView } from "@/components/views/TaxView";

function One() {
  return <TaxView household={useSearchParams().get("household")} />;
}

export default function Tax() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
