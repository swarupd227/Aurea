"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getToken, getUser } from "@/lib/api";
import { roleLanding } from "@/lib/roles";

export default function Home() {
  const router = useRouter();
  useEffect(() => {
    if (!getToken()) router.replace("/login");
    else {
      const u = getUser();
      router.replace(roleLanding(u?.role || "adviser"));
    }
  }, [router]);
  return null;
}
