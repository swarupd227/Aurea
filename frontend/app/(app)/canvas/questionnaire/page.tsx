"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { CanvasQuestionnaireView } from "@/components/views/CanvasQuestionnaireView";

function One() {
  const params = useSearchParams();
  return (
    <CanvasQuestionnaireView
      household={params.get("household") ?? params.get("household_id")}
      subject={params.get("subject_id")}
    />
  );
}

export default function QuestionnairePage() {
  return (
    <Suspense fallback={null}>
      <One />
    </Suspense>
  );
}
