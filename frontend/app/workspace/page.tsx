"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createThread, fetchMentionables, sendMessage } from "./api";
import { useWorkspace } from "./context";
import { ThreadView } from "./components/ThreadView";
import type { Mentionable } from "./types";

export default function WorkspaceHomePage() {
  const router = useRouter();
  const { composerInsert, refetchThreads } = useWorkspace();
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mentionables, setMentionables] = useState<Mentionable[]>([]);

  useEffect(() => {
    fetchMentionables().then((d) => setMentionables(d.mentionables)).catch(() => {});
  }, []);

  const handleSend = async (text: string) => {
    if (creating) return;
    setCreating(true);
    setError(null);
    try {
      const thread = await createThread({ kind: "ask_astra", title: text.slice(0, 60) });
      await sendMessage(thread.id, text);
      refetchThreads();
      router.push(`/workspace/${thread.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start conversation");
      setCreating(false);
    }
  };

  return (
    <ThreadView
      messages={[]}
      status="idle"
      streaming={creating}
      error={error}
      hasThread={false}
      onSend={handleSend}
      onConfirm={async () => {}}
      onReject={async () => {}}
      mentionables={mentionables}
      composerInsert={composerInsert}
    />
  );
}
