"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

export function useApi<T = any>(path: string | null, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    setError(null);
    try {
      setData(await api<T>(path));
    } catch (e: any) {
      setError(e.message || "Failed to load");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  // A change confirmed in the docked conversation (or any other surface that announces one) means
  // the figures on this page may be stale: load them again.
  useEffect(() => {
    window.addEventListener("astra:changed", refetch);
    return () => window.removeEventListener("astra:changed", refetch);
  }, [refetch]);

  return { data, loading, error, refetch, setData };
}
