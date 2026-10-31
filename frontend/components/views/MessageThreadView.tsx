"use client";

import { Breadcrumb, Card, ErrorState, Spinner } from "@/components/ui";
import { MessageTimeline } from "@/components/MessageThread";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { withRef } from "@/lib/refs";
import { ViewLink, useViewNav } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";

/** One client's secure messages. Opening it clears its unread for those who reply. In the work pane
 * a reply is the conversation's tool, behind a Confirm card that shows the words; on a page it sends. */
export function MessageThreadView({ householdId }: { householdId: string }) {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<any>(`/api/engage/messages/${householdId}`, [householdId]);

  if (loading && !data) return <Spinner />;
  if (error || !data) return <Card><ErrorState what="this thread" message={error ?? "Not found"} onRetry={refetch} /></Card>;

  async function send(text: string) {
    if (nav.say) return nav.say(withRef(`Send this message to ${data.household_name}, word for word: ${text}`, "household", householdId));
    await api("/api/canvas/messages", { body: { body: text, household_id: householdId } });
    await refetch();
  }

  return (
    <div className="view-container">
      {!nav.inPane && <Breadcrumb items={[{ label: "Messages", href: "/studio/messages" }, { label: data.household_name }]} />}
      <ViewHeader
        title={data.household_name}
        meta={
          <>
            <span className={`${chip} tabular-nums`}>{data.messages.length} messages</span>
            <ViewLink href={`/studio/clients/${householdId}`} className={`${chip} hover:text-ink`}>Open client</ViewLink>
          </>
        }
      />
      <Card><MessageTimeline messages={data.messages} onSend={send} /></Card>
    </div>
  );
}
