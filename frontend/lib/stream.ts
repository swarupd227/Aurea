// Consume Atlas SSE endpoints via fetch (so we can send the bearer token).
import { API_BASE, getToken } from "./api";

/** Generic SSE consumer for any Atlas streaming endpoint. */
export async function streamSSE(path: string, onEvent: (ev: any) => void): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!res.ok || !res.body) {
    onEvent({ phase: "error", message: `HTTP ${res.status}` });
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const line = part.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(5).trim()));
      } catch {}
    }
  }
}

export async function streamRun(
  params: { agent_key: string; subject_type?: string; subject_id?: string },
  onEvent: (ev: any) => void
): Promise<void> {
  const qs = new URLSearchParams();
  qs.set("agent_key", params.agent_key);
  if (params.subject_type) qs.set("subject_type", params.subject_type);
  if (params.subject_id) qs.set("subject_id", params.subject_id);
  return streamSSE(`/api/atlas/run-stream?${qs.toString()}`, onEvent);
}

export async function streamSkill(
  params: { skill_id: string; test?: boolean; subject_type?: string; subject_id?: string },
  onEvent: (ev: any) => void
): Promise<void> {
  const qs = new URLSearchParams();
  if (params.test) qs.set("test", "true");
  if (params.subject_type) qs.set("subject_type", params.subject_type);
  if (params.subject_id) qs.set("subject_id", params.subject_id);
  return streamSSE(`/api/skills/${params.skill_id}/run-stream?${qs.toString()}`, onEvent);
}
