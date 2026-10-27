import { createThread } from "../api";

const KEY = (threadId: string) => `astra:first:${threadId}`;

/** Create a conversation whose first message is `text`, and say where to go. The sentence is
 * handed to the thread page through sessionStorage, not the URL - it may name a client - and the
 * thread page sends it, so the turn streams in view instead of finishing off-screen. */
export async function startConversation(text: string): Promise<string> {
  const thread = await createThread({ kind: "ask_astra", title: text.slice(0, 60) });
  try {
    sessionStorage.setItem(KEY(thread.id), text);
  } catch {
    // Storage blocked: the thread opens empty and the user can resend. Nothing is lost silently.
  }
  return `/workspace/${thread.id}`;
}

/** Take (and clear) the first message waiting for this thread, if any. */
export function takeFirstMessage(threadId: string): string | null {
  try {
    const text = sessionStorage.getItem(KEY(threadId));
    if (text) sessionStorage.removeItem(KEY(threadId));
    return text;
  } catch {
    return null;
  }
}
