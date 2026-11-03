"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { CanvasRetirementView } from "@/components/views/CanvasRetirementView";

function One() {
  const q = useSearchParams();
  return <CanvasRetirementView household={q.get("household") ?? q.get("household_id")} />;
}

export default function CanvasRetirement() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
