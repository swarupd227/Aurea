"use client";
import { useEffect, useState } from "react";
import { PiggyBank } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import RetirementPlanner from "@/components/RetirementPlanner";
import { Spinner } from "@/components/ui";
import { api, getUser } from "@/lib/api";

export default function CanvasRetirement() {
  const user = typeof window !== "undefined" ? getUser() : null;
  const isClient = user?.role === "client";
  const [households, setHouseholds] = useState<any[]>([]);
  const [hid, setHid] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    (async () => {
      if (!isClient) {
        const hh = await api("/api/core/households");
        setHouseholds(hh);
        setHid(hh[0]?.id || null);
      }
      setReady(true);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!ready) return <Spinner />;

  return (
    <div>
      <PageHeader
        title="Will I be okay in retirement?"
        sub="A plain-language view of whether your savings will support the lifestyle you want — and what would help."
        actions={
          !isClient && households.length > 0 ? (
            <select className="input" value={hid || ""} onChange={(e) => setHid(e.target.value)}>
              {households.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
            </select>
          ) : undefined
        }
      />
      {isClient ? (
        <RetirementPlanner basePath="/api/canvas/retirement" client />
      ) : hid ? (
        <RetirementPlanner basePath="/api/canvas/retirement" client query={{ household_id: hid }} />
      ) : (
        <div className="text-sm text-ink-muted">No household selected.</div>
      )}
    </div>
  );
}
