/**Workspace API client — thin wrapper over the shared, token-aware `api()` client.*/

import { api } from "@/lib/api";
import type { Briefing, Mentionable, NeedsYou, ThreadDetail, ThreadSummary } from "./types";

export async function fetchThreads(): Promise<{ threads: ThreadSummary[] }> {
  return api("/api/threads");
}

export async function createThread(data: {
  kind: string;
  title?: string;
  subject_id?: string;
}): Promise<ThreadSummary> {
  return api("/api/threads", { body: data });
}

export async function fetchThread(threadId: string): Promise<ThreadDetail> {
  return api(`/api/threads/${threadId}`);
}

export async function sendMessage(threadId: string, text: string): Promise<any> {
  return api(`/api/threads/${threadId}/send`, { body: { text } });
}

export async function confirmPendingAction(
  pendingActionId: string,
  confirmed: boolean
): Promise<any> {
  return api(`/api/threads/pending-actions/${pendingActionId}/confirm`, {
    body: { confirmed },
  });
}

export async function fetchNeedsYou(): Promise<NeedsYou> {
  return api("/api/threads/needs-you");
}

export async function fetchBriefing(): Promise<Briefing> {
  return api("/api/threads/briefing");
}

export async function fetchMentionables(): Promise<{ mentionables: Mentionable[] }> {
  return api("/api/threads/mentionables");
}
