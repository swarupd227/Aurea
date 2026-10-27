"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { withView } from "@/lib/paneViews";
import { fetchMentionables } from "./api";
import { useWorkspace } from "./context";
import { ThreadView } from "./components/ThreadView";
import { startConversation } from "./lib/start";
import type { Mentionable } from "./types";

export default function WorkspaceHomePage() {
  const router = useRouter();
  const { composerInsert, refetchThreads, pane } = useWorkspace();
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
      // The thread page sends the first message, so the turn streams in view with its steps.
      const path = await startConversation(text);
      refetchThreads();
      router.push(withView(path, pane?.route ?? null)); // the open view stays open
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
