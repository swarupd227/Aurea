"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ClientFileView } from "@/components/views/ClientFileView";

function One() {
  return <ClientFileView household={useSearchParams().get("household")} />;
}

export default function ClientFile() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
