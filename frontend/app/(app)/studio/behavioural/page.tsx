"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { BehaviouralView } from "@/components/views/BehaviouralView";

function One() {
  return <BehaviouralView household={useSearchParams().get("household")} />;
}

export default function Behavioural() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
