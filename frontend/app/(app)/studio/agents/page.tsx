"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

// 'Agents' merged into 'Workforce'. Redirect any old links.
export default function AgentsRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/studio/workforce");
  }, [router]);
  return null;
}
