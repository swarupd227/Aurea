"use client";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { BillingView } from "@/components/views/BillingView";

// The page and the workspace's work pane render the same view (components/views).
function BillingPage() {
  const invoice = useSearchParams().get("invoice");
  return <BillingView focusInvoice={invoice} />;
}

export default function Billing() {
  return (
    <Suspense fallback={null}>
      <BillingPage />
    </Suspense>
  );
}
