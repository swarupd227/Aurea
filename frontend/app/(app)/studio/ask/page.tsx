"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

// 'Ask your book' is the conversation now: Astra answers a question about the whole book with the
// screen_book tool, not a snapshot in a prompt. Redirect any old links.
export default function AskRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/workspace");
  }, [router]);
  return null;
}
