/**Workspace API client — thin wrapper over the shared, token-aware `api()` client.*/

import { api } from "@/lib/api";

export async function fetchThreads() {
  return api<{ threads: any[] }>("/api/threads");
}

export async function createThread(data: {
  kind: string;
  title: string;
  subject_id?: string;
}) {
  return api("/api/threads", { body: data });
}

export async function fetchThread(threadId: string) {
  return api(`/api/threads/${threadId}`);
}

export async function sendMessage(threadId: string, text: string) {
  return api(`/api/threads/${threadId}/send`, { body: { text } });
}

export async function confirmPendingAction(
  pendingActionId: string,
  confirmed: boolean
) {
  return api(`/api/threads/pending-actions/${pendingActionId}/confirm`, {
    body: { confirmed },
  });
}
