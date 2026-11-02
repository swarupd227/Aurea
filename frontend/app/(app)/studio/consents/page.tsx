"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ConsentsView } from "@/components/views/ConsentsView";

function One() {
  return <ConsentsView household={useSearchParams().get("household")} />;
}

export default function Consents() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
