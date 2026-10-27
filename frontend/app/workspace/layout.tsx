"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Plus, Search } from "lucide-react";
import { getToken, getUser } from "@/lib/api";
import { hasWorkspace } from "@/lib/nav";
import { roleLanding } from "@/lib/roles";
import { CommandPalette } from "@/components/CommandPalette";
import { WorkspaceProvider, useWorkspace } from "./context";
import { Rail } from "./components/Rail";

function WorkspaceShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { threads, mention, askAstra } = useWorkspace();

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
          onAsk={(text) => void askAstra(text)}
          view={threadId ? "thread" : "home"}
        />
      </div>

      <main className="flex min-h-0 min-w-0 flex-col">
        <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-4">
          <h1 className="min-w-0 flex-1 truncate text-sm font-medium text-ink">
            {threadId ? activeThread?.title || "Conversation" : "New conversation"}
          </h1>
          <button
            onClick={() => window.dispatchEvent(new Event("astra:open-palette"))}
            aria-label="Search and ask"
            className="flex items-center gap-1 rounded px-2 py-1 text-xs text-ink-muted hover:bg-border-soft md:hidden"
          >
            <Search className="h-3.5 w-3.5" />
          </button>
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
    // Clients and the platform owner hold no conversation tools; send them to their own page.
    const role = getUser()?.role;
    if (role && !hasWorkspace(role)) {
      router.replace(roleLanding(role));
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
