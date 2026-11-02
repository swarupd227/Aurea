"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { FamilyView } from "@/components/views/FamilyView";

function One() {
  return <FamilyView household={useSearchParams().get("household")} />;
}

export default function Family() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
