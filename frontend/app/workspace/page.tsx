"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createThread, fetchMentionables, sendMessageStream } from "./api";
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
      // Streamed but not rendered here — we navigate to the thread page as soon
      // as it's created, which shows this same turn streaming live once mounted.
      let streamError: string | null = null;
      await sendMessageStream(thread.id, text, (event) => {
        if (event.type === "error") streamError = event.message;
      });
      refetchThreads();
      router.push(`/workspace/${thread.id}`);
      if (streamError) setError(streamError);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start conversation");
      setCreating(false);
    }
  };

  return (
    <ThreadView
      messages={[]}
      status="idle"
      live={creating ? { text: "", steps: [] } : null}
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
