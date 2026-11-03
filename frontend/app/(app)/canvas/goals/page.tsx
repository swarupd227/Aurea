"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { CanvasGoalsView } from "@/components/views/CanvasGoalsView";

function One() {
  const q = useSearchParams();
  return <CanvasGoalsView household={q.get("household") ?? q.get("household_id")} />;
}

export default function CanvasGoals() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
