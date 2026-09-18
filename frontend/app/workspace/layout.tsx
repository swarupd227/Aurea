"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ArrowLeft, Plus } from "lucide-react";
import { getToken } from "@/lib/api";
import { WorkspaceProvider, useWorkspace } from "./context";
import { Rail } from "./components/Rail";
import { CommandPalette } from "./components/CommandPalette";

function WorkspaceShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { threads, mention } = useWorkspace();

  const threadId = pathname.startsWith("/workspace/") ? pathname.slice("/workspace/".length) : null;
  const activeThread = threadId ? threads.find((t) => t.id === threadId) : null;

  const newConversation = () => router.push("/workspace");

  return (
    <div className="grid h-screen w-full overflow-hidden bg-paper text-ink md:grid-cols-[260px_minmax(0,1fr)]">
      <div className="hidden min-h-0 border-r border-border md:block">
        <Rail
          threads={threads}
          activeId={threadId}
          onSelect={(id) => router.push(`/workspace/${id}`)}
          onNew={newConversation}
          onMention={mention}
          view={threadId ? "thread" : "home"}
        />
      </div>

      <main className="flex min-h-0 min-w-0 flex-col">
        <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-4">
          <button onClick={() => router.push("/studio")} className="md:hidden" aria-label="Back to the classic app">
            <ArrowLeft className="h-4 w-4 text-ink-muted" />
          </button>
          <h1 className="min-w-0 flex-1 truncate text-sm font-medium text-ink">
            {threadId ? activeThread?.title || "Conversation" : "New conversation"}
          </h1>
          <button
            onClick={newConversation}
            className="flex items-center gap-1 rounded px-2 py-1 text-xs text-ink-muted hover:bg-border-soft md:hidden"
          >
            <Plus className="h-3.5 w-3.5" /> New
          </button>
        </header>
        <div className="min-h-0 flex-1">{children}</div>
      </main>

      <CommandPalette />
    </div>
  );
}

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    setReady(true);
  }, [router]);

  if (!ready) return <div className="h-screen bg-paper" />;

  return (
    <WorkspaceProvider>
      <WorkspaceShell>{children}</WorkspaceShell>
    </WorkspaceProvider>
  );
}
