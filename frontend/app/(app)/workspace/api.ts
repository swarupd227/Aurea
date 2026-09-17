/**API client for workspace endpoints — uses NEXT_PUBLIC_API_BASE to reach backend.*/

const getApiBase = () => {
  if (typeof window !== 'undefined') {
    return process.env.NEXT_PUBLIC_API_BASE || '';
  }
  return process.env.NEXT_PUBLIC_API_BASE || '';
};

export async function fetchThreads() {
  const res = await fetch(`${getApiBase()}/api/threads`);
  if (!res.ok) throw new Error("Failed to load threads");
  return res.json();
}

export async function createThread(data: {
  kind: string;
  title: string;
  subject_id?: string;
}) {
  const res = await fetch(`${getApiBase()}/api/threads`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to create thread");
  return res.json();
}

export async function fetchThread(threadId: string) {
  const res = await fetch(`${getApiBase()}/api/threads/${threadId}`);
  if (!res.ok) throw new Error("Failed to load thread");
  return res.json();
}

export async function sendMessage(
  threadId: string,
  text: string
) {
  const res = await fetch(`${getApiBase()}/api/threads/${threadId}/send`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error("Failed to send message");
  return res.json();
}

export async function confirmPendingAction(
  pendingActionId: string,
  confirmed: boolean
) {
  const res = await fetch(
    `${getApiBase()}/api/threads/pending-actions/${pendingActionId}/confirm`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmed }),
    }
  );
  if (!res.ok) throw new Error("Failed to confirm action");
  return res.json();
}
