import { Markdown } from "./Markdown";
import { ConfirmationCard } from "./ConfirmationCard";
import { Sources } from "./Sources";
import { ArtifactRenderer } from "./artifacts/ArtifactRenderer";
import type { Message } from "../types";

function AstraMark() {
  return (
    <div
      aria-hidden
      className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded bg-accent font-mono text-[11px] font-bold text-ink"
    >
      A
    </div>
  );
}

export function MessageView({
  message,
  onConfirm,
  onReject,
}: {
  message: Message;
  onConfirm: (id: string) => Promise<void>;
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

  return (
    <div className="flex gap-3">
      <AstraMark />
      <div className="min-w-0 flex-1 space-y-3">
        {message.text && <Markdown text={message.text} className="text-sm text-ink" />}

        {message.artifacts.map((a, i) => (
          <ArtifactRenderer key={i} artifact={a} />
        ))}

        {message.pending_action && (
          <ConfirmationCard action={message.pending_action} onConfirm={onConfirm} onReject={onReject} />
        )}

        <Sources tools={message.sources} />
      </div>
    </div>
  );
}
