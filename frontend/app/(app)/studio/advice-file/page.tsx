"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { AdviceFileView } from "@/components/views/AdviceFileView";

function One() {
  return <AdviceFileView household={useSearchParams().get("household")} />;
}

export default function AdviceFile() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
