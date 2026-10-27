import type { LiveTurn, StreamEvent } from "../types";

/** Fold one streamed event into the turn being shown live. Shared by the thread page and the
 * page-scoped conversation so both narrate a run the same way. "done", "pending_action" and
 * "error" leave the live turn alone: the caller reloads the persisted thread, which is authoritative. */
export function reduceLive(prev: LiveTurn | null, event: StreamEvent): LiveTurn | null {
  const base: LiveTurn = prev ?? { text: "", steps: [] };
  switch (event.type) {
    case "token":
      return { ...base, text: base.text + event.text };
    case "tool_start":
      return { ...base, steps: [...base.steps, { tool_key: event.tool_key, state: "running" }] };
    case "tool_result":
      return {
        ...base,
        steps: base.steps.map((s) =>
          s.tool_key === event.tool_key && s.state === "running" ? { ...s, state: event.ok ? "ok" : "failed" } : s
        ),
      };
    default:
      return prev;
  }
}
