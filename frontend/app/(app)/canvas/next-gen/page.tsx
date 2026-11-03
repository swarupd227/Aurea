"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { CanvasNextGenView } from "@/components/views/CanvasNextGenView";

function One() {
  return <CanvasNextGenView household={useSearchParams().get("household")} />;
}

export default function NextGenJourney() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
