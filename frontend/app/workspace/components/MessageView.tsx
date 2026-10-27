import { useRouter } from "next/navigation";
import { PanelRight } from "lucide-react";
import { getUser } from "@/lib/api";
import { matchPaneView, paneRouteForArtifact } from "@/lib/paneViews";
import { readable } from "@/lib/refs";
import { Markdown } from "./Markdown";
import { ConfirmationCard } from "./ConfirmationCard";
import { ProofStrip } from "./ProofStrip";
import { artifactCard, MessageCardRenderer } from "./artifacts/ArtifactRenderer";
import type { Message } from "../types";

function Mark({ letter, working }: { letter: string; working: boolean }) {
  return (
    <div
      aria-hidden
      className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded bg-accent font-mono text-[11px] font-bold text-black ${
        working ? "agent-working" : ""
      }`}
    >
      {letter}
    </div>
  );
}

/** "Open" on a card whose data is a view: in the work pane beside the conversation when there is
 * one, as its page otherwise (the docked conversation on a classic page). */
function OpenView({ route, onOpenView }: { route: string; onOpenView?: (route: string) => void }) {
  const router = useRouter();
  const label = matchPaneView(route)?.label ?? "view";
  return (
    <button
      type="button"
      data-testid="card-open-view"
      data-route={route}
      onClick={() => (onOpenView ? onOpenView(route) : router.push(route))}
      className="inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-0.5 text-xs text-ink-soft transition-colors hover:border-accent/60 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      title={onOpenView ? `Open ${label} beside the conversation` : `Open ${label}`}
    >
      <PanelRight className="h-3 w-3 text-ink-muted" aria-hidden /> Open {label}
    </button>
  );
}

export function MessageView({
  message,
  onConfirm,
  onReject,
  onOpenView,
}: {
  message: Message;
  onConfirm: (id: string, seenTier?: string) => Promise<void>;
  onReject: (id: string) => Promise<void>;
  /** Opens a pane view beside the conversation. Absent outside the workspace. */
  onOpenView?: (route: string) => void;
}) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-lg bg-accent/15 border border-accent/20 px-3.5 py-2 text-sm text-ink">
          {readable(message.text)}
        </div>
      </div>
    );
  }

  // A message from an agent (a run settling) carries the agent's name; Astra's carries none.
  const isAgent = message.role === "agent";
  const agentName: string | null = isAgent
    ? message.card?.data?.agent_name ?? (message.speaking_agent ?? "").replace(/_/g, " ")
    : null;
  const working = message.card?.kind === "agent_run" && message.card.data?.status === "running";

  // Which results open as a view: only pane views the reader's role may see. Ids come from the
  // tool results, never from the reply's prose. Each view is offered once per message.
  const role = typeof window !== "undefined" ? getUser()?.role ?? "" : "";
  const viewable = (route: string | null) => (route && matchPaneView(route, role) ? route : null);
  const offered = new Set<string>();
  const cards = message.artifacts.map((a) => {
    const route = viewable(paneRouteForArtifact(a.tool_key, a.result));
    const node = artifactCard(a);
    const open = route && node && !offered.has(route) ? route : null;
    if (open) offered.add(open);
    return { node, route, open };
  });
  const loose = Array.from(new Set(cards.filter((c) => c.route && !c.node).map((c) => c.route as string))).filter(
    (r) => !offered.has(r)
  );
  const runRoute = message.card?.kind === "agent_run" ? viewable("/studio") : null;

  return (
    <div className="flex gap-3" data-testid={isAgent ? "agent-message" : undefined}>
      <Mark letter={(agentName || "A").charAt(0).toUpperCase()} working={working} />
      <div className="min-w-0 flex-1 space-y-3">
        {agentName && (
          <div className="font-mono text-[11px] uppercase tracking-wider text-ink-muted">{agentName}</div>
        )}
        {message.text && <Markdown text={message.text} className="text-sm text-ink" />}

        {message.card && (
          <div className="space-y-1.5">
            <MessageCardRenderer card={message.card} />
            {runRoute && !working && (
              <div className="flex justify-end">
                <OpenView route={runRoute} onOpenView={onOpenView} />
              </div>
            )}
          </div>
        )}

        {cards.map((c, i) =>
          c.node ? (
            <div key={i} className="space-y-1.5">
              {c.node}
              {c.open && (
                <div className="flex justify-end">
                  <OpenView route={c.open} onOpenView={onOpenView} />
                </div>
              )}
            </div>
          ) : null
        )}

        {loose.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {loose.map((r) => (
              <OpenView key={r} route={r} onOpenView={onOpenView} />
            ))}
          </div>
        )}

        {message.pending_action && (
          <ConfirmationCard action={message.pending_action} onConfirm={onConfirm} onReject={onReject} />
        )}

        <ProofStrip tools={message.sources} artifacts={message.artifacts} />
      </div>
    </div>
  );
}
