"use client";
import { useState } from "react";
import { MessageSquare } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner, Empty } from "@/components/ui";
import MessageThread from "@/components/MessageThread";
import { useApi } from "@/lib/hooks";
import { timeAgo } from "@/lib/format";

export default function StudioMessages() {
  const { data: threads, loading } = useApi<any[]>("/api/engage/messages");
  const [active, setActive] = useState<any>(null);

  return (
    <div>
      <PageHeader title="Messages" sub="Secure threads with your clients." />
      {loading ? (
        <Spinner />
      ) : !threads?.length ? (
        <Card><Empty>No client messages yet.</Empty></Card>
      ) : (
        <div className="grid md:grid-cols-3 gap-5">
          <div className="space-y-2">
            {threads.map((t) => (
              <button key={t.household_id} onClick={() => setActive(t)}
                      className={`w-full text-left card p-4 transition ${active?.household_id === t.household_id ? "border-navy-400" : "hover:border-navy-200"}`}>
                <div className="flex items-center justify-between">
                  <span className="font-medium text-ink text-sm">{t.household_name}</span>
                  {t.unread > 0 && <span className="chip bg-gold text-ink text-[10px]">{t.unread}</span>}
                </div>
                <div className="text-xs text-ink-muted mt-1 truncate">{t.last_body}</div>
                <div className="text-[11px] text-ink-muted mt-0.5">{timeAgo(t.last_at)}</div>
              </button>
            ))}
          </div>
          <div className="md:col-span-2">
            {active ? (
              <Card className="p-5">
                <div className="font-semibold text-ink mb-3 flex items-center gap-2"><MessageSquare size={17} /> {active.household_name}</div>
                <MessageThread householdId={active.household_id} />
              </Card>
            ) : (
              <Card><Empty>Select a thread to reply.</Empty></Card>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
