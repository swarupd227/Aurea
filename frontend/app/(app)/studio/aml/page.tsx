"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { AmlReviewsView } from "@/components/views/AmlReviewsView";

function One() {
  return <AmlReviewsView caseId={useSearchParams().get("case")} />;
}

export default function AmlReviews() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
