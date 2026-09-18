export function Sources({ tools }: { tools: string[] }) {
  if (tools.length === 0) return null;
  return (
    <div className="border-t border-border-soft pt-2 font-mono text-[11px] text-ink-muted">
      Used {tools.map((t) => t.replace(/_/g, " ")).join(" · ")}
    </div>
  );
}
