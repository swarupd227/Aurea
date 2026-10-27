import { Markdown } from "./Markdown";
import { ConfirmationCard } from "./ConfirmationCard";
import { ProofStrip } from "./ProofStrip";
import { ArtifactRenderer, MessageCardRenderer } from "./artifacts/ArtifactRenderer";
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

export function MessageView({
  message,
  onConfirm,
  onReject,
}: {
  message: Message;
  onConfirm: (id: string, seenTier?: string) => Promise<void>;
  onReject: (id: string) => Promise<void>;
}) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-lg bg-accent/15 border border-accent/20 px-3.5 py-2 text-sm text-ink">
          {message.text}
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

  return (
    <div className="flex gap-3" data-testid={isAgent ? "agent-message" : undefined}>
      <Mark letter={(agentName || "A").charAt(0).toUpperCase()} working={working} />
      <div className="min-w-0 flex-1 space-y-3">
        {agentName && (
          <div className="font-mono text-[11px] uppercase tracking-wider text-ink-muted">{agentName}</div>
        )}
        {message.text && <Markdown text={message.text} className="text-sm text-ink" />}

        {message.card && <MessageCardRenderer card={message.card} />}

        {message.artifacts.map((a, i) => (
          <ArtifactRenderer key={i} artifact={a} />
        ))}

        {message.pending_action && (
          <ConfirmationCard action={message.pending_action} onConfirm={onConfirm} onReject={onReject} />
        )}

        <ProofStrip tools={message.sources} artifacts={message.artifacts} />
      </div>
    </div>
  );
}
