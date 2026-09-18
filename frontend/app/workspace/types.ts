export interface PendingActionRef {
  id: string;
  tool_key: string;
  confirmation_text: string;
  decision: "confirmed" | "declined" | null;
}

export interface Message {
  id: string;
  role: "user" | "astra" | "agent";
  speaking_agent: string | null;
  text: string;
  sources: string[];
  pending_action: PendingActionRef | null;
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

export interface NeedsYou {
  needs_decision_count: number;
  needs_decision: NeedsYouItem[];
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
