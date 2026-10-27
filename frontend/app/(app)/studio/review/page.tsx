"use client";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { RecommendationsView } from "@/components/views/RecommendationsView";

// The page and the workspace's work pane render the same view (components/views).
function Review() {
  const rec = useSearchParams().get("rec");
  return <RecommendationsView focusId={rec} />;
}

export default function ReviewQueue() {
  return (
    <Suspense fallback={null}>
      <Review />
    </Suspense>
  );
}
