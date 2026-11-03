"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { AdminView } from "@/components/views/AdminView";

function One() {
  const q = useSearchParams();
  return <AdminView tab={q.get("tab")} />;
}

export default function Admin() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
