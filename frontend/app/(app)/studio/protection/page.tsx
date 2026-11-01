"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ProtectionView } from "@/components/views/ProtectionView";

function One() {
  return <ProtectionView household={useSearchParams().get("household")} />;
}

export default function Protection() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
