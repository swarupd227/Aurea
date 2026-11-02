"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { EstateView } from "@/components/views/EstateView";

function One() {
  return <EstateView household={useSearchParams().get("household")} />;
}

export default function Estate() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
