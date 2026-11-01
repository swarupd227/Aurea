"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ReviewsView } from "@/components/views/ReviewsView";

function One() {
  return <ReviewsView household={useSearchParams().get("household")} />;
}

export default function ClientReviews() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
