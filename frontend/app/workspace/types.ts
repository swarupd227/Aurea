/** What confirming a run would start. Read from live policy by the server, never typed by the
 * model: the three controls are separate fields because they are separate decisions. */
export interface RunPreview {
  agent_key: string;
  agent_name: string;
  stage: string | null;
  does: string | null;
  subject_label: string | null;
  confirms: "start_only";
  autonomy: { tier: string; label: string; acts_on_its_own: boolean; held_to_proposal: boolean };
  decision: { roles: string[]; any_staff: boolean; you_may_decide: boolean };
}

/** The record a change would touch and what it would become, read by the server when the card shows. */
export interface ChangePreview {
  rows: { label: string; value: string }[];
}

export interface PendingActionRef {
  id: string;
  tool_key: string;
  confirmation_text: string;
  decision: "confirmed" | "declined" | "expired" | "failed" | null;
  /** Present for a run or a task: what confirming does, or the reason it no longer can. */
  preview?: (RunPreview & { unavailable?: undefined }) | ChangePreview | { unavailable: string } | null;
}

/** A card that belongs to a message rather than to one of its tool calls: a run, narrated. */
export interface MessageCard {
  kind: string;
  data: Record<string, any>;
}

export interface Artifact {
  tool_key: string;
  result: Record<string, any>;
}

export interface Message {
  id: string;
  role: "user" | "astra" | "agent";
  speaking_agent: string | null;
  text: string;
  sources: string[];
  artifacts: Artifact[];
  pending_action: PendingActionRef | null;
  card?: MessageCard | null;
  suggestions: string[];
  created_at: string;
}

export type ThreadStatus = "idle" | "awaiting_confirmation";

export interface ThreadSummary {
  id: string;
  kind: string;
  title: string | null;
  status: ThreadStatus;
  created_at: string;
  updated_at: string;
}

export interface ThreadDetail extends ThreadSummary {
  /** A reply is being written to this thread right now (the server keeps going if the browser leaves). */
  working?: boolean;
  messages: Message[];
}

export interface NeedsYouItem {
  id: string;
  thread_id: string;
  thread_title: string | null;
  title: string;
  tool_key: string;
  created_at: string;
}

/** An open agent proposal this role may decide (the server applies decision rights). */
export interface NeedsYouProposal {
  id: string;
  title: string;
  agent_key: string;
  subject_label: string | null;
  priority: number;
  /** "decide": the role may approve it. "veto": its standing is to dismiss or roll back (compliance, on money). */
  action?: "decide" | "veto";
  created_at: string | null;
}

export interface NeedsYou {
  needs_decision_count: number;
  needs_decision: NeedsYouItem[];
  proposals_count?: number;
  proposals?: NeedsYouProposal[];
}

export interface BriefingRow {
  id: string;
  label: string;
  count: number;
  detail: string | null;
  prompt: string;
  tone: "attention" | "neutral";
}

export interface Briefing {
  rows: BriefingRow[];
  not_shown: string[];
}

export interface Mentionable {
  id: string;
  name: string;
  description: string;
}

export interface LiveStep {
  tool_key: string;
  state: "running" | "ok" | "failed";
}

export interface LiveTurn {
  text: string;
  steps: LiveStep[];
}

export type StreamEvent =
  | { type: "token"; text: string }
  | { type: "tool_start"; tool_key: string }
  | { type: "tool_result"; tool_key: string; ok: boolean }
  | { type: "pending_action"; pending_action_id: string; tool_key: string; confirmation_text: string; preview?: RunPreview | ChangePreview }
  | { type: "done"; message_id: string }
  | { type: "error"; message: string };
