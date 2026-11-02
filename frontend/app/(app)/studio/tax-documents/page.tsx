"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { TaxDocumentsView } from "@/components/views/TradesView";

function One() {
  return <TaxDocumentsView household={useSearchParams().get("household")} />;
}

export default function TaxDocuments() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
