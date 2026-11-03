"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { CanvasView } from "@/components/views/CanvasView";

function One() {
  const params = useSearchParams();
  // "View as client" links used to carry ?household_id=; ?household= is the view's own.
  return <CanvasView household={params.get("household") ?? params.get("household_id")} />;
}

export default function Canvas() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
