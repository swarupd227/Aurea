/**Workspace API client — thin wrapper over the shared, token-aware `api()` client.*/

import { api, API_BASE, getToken } from "@/lib/api";
import type { Briefing, Mentionable, NeedsYou, StreamEvent, ThreadDetail, ThreadSummary } from "./types";

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

/**
 * Send a message and stream Astra's turn back event by event (SSE, one JSON
 * object per `data:` line). Uses fetch()+ReadableStream rather than the
 * browser's native EventSource, which can't send an Authorization header.
 * onEvent is called once per parsed event; the promise resolves once the
 * stream ends (naturally, via "done"/"pending_action"/"error", or the
 * connection closing).
 */
export async function sendMessageStream(
  threadId: string,
  text: string,
  onEvent: (event: StreamEvent) => void
): Promise<void> {
  const token = getToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}/api/threads/${threadId}/send`, {
    method: "POST",
    headers,
    body: JSON.stringify({ text }),
  });

  if (!res.ok || !res.body) {
    let detail = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      if (j?.detail) detail = j.detail;
    } catch {}
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(5).trim()) as StreamEvent);
      } catch {
        // Malformed chunk — skip rather than break the whole stream.
      }
    }
  }
}

/** `seenTier` is the autonomy tier the confirmation showed for a run; the server refuses if the
 * firm's policy has changed since, rather than start the run at a tier the user did not see. */
export async function confirmPendingAction(
  pendingActionId: string,
  confirmed: boolean,
  seenTier?: string
): Promise<any> {
  // The answer is given the moment the button is pressed, but a confirmed run is only reported
  // back when it finishes. Tell the rail now, so Needs-you stops asking a question already
  // answered; if the server refuses, "astra:changed" re-reads what is really waiting.
  window.dispatchEvent(new CustomEvent("astra:pending-answered", { detail: pendingActionId }));
  try {
    return await api(`/api/threads/pending-actions/${pendingActionId}/confirm`, {
      body: seenTier ? { confirmed, seen_tier: seenTier } : { confirmed },
    });
  } catch (err) {
    window.dispatchEvent(new Event("astra:changed"));
    throw err;
  }
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
