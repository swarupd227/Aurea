"use client";
import { useParams } from "next/navigation";
import { OnboardingCaseView } from "@/components/views/OnboardingCaseView";

// The page and the workspace's work pane render the same view (components/views).
export default function OnboardingCase() {
  const { id } = useParams<{ id: string }>();
  return <OnboardingCaseView key={id} id={id} />;
}
