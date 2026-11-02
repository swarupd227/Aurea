"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ExitsView } from "@/components/views/ExitsView";

function One() {
  return <ExitsView household={useSearchParams().get("household")} />;
}

export default function Exits() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
