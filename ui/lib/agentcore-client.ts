/**
 * Client helper to call the Next.js /api/agentcore route.
 * Use from chat UI when "Cloud Intelligence Agent" is selected.
 */

export type AgentcorePayload = {
  prompt: string;
  sessionId?: string;
  scope?: "cost" | "logs" | "audit" | "all";
  stream?: boolean;
};

export type AgentcoreResponse = {
  result?: string;
  messages?: unknown[];
  error?: string;
  details?: string;
};

export async function sendToAgentcore(
  payload: AgentcorePayload
): Promise<AgentcoreResponse> {
  const res = await fetch("/api/agentcore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt: payload.prompt,
      sessionId: payload.sessionId,
      scope: payload.scope,
      stream: payload.stream ?? false,
    }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

/** For streaming: use response.body with ReadableStream (e.g. getReader(), read()). */
export function getAgentcoreStream(payload: AgentcorePayload): Promise<Response> {
  return fetch("/api/agentcore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt: payload.prompt,
      sessionId: payload.sessionId,
      scope: payload.scope,
      stream: true,
    }),
  });
}
