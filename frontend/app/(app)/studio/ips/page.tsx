"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { IpsView } from "@/components/views/IpsView";

function One() {
  return <IpsView household={useSearchParams().get("household")} />;
}

export default function IpsMonitoring() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
