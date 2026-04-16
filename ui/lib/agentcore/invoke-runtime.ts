import {
  consumeAgentcoreRuntimeStream,
  iterateAgentcoreRuntimeEvents,
} from "./parse-runtime-sse";

async function postAgentcoreRuntime(
  url: string,
  params: { prompt: string; sessionId: string; signal?: AbortSignal }
) {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  const auth = process.env.AGENTCORE_AUTH_HEADER;
  if (auth) headers.Authorization = auth;

  const res = await fetch(url, {
    method: "POST",
    headers,
    signal: params.signal,
    body: JSON.stringify({
      prompt: params.prompt,
      session_id: params.sessionId,
      sessionId: params.sessionId,
    }),
  });

  if (!res.ok) {
    const t = await res.text().catch(() => "");
    throw new Error(`AgentCore HTTP ${res.status}: ${t.slice(0, 500)}`);
  }

  if (!res.body) {
    throw new Error("AgentCore response has no body");
  }

  return res.body;
}

function requireRuntimeUrl(): string {
  const url = process.env.AGENTCORE_RUNTIME_INVOKE_URL;
  if (!url) {
    throw new Error(
      "AGENTCORE_RUNTIME_INVOKE_URL is not set. For local agent: run `pip install -e .` in agent/, then `PYTHONPATH=src python src/main.py` and set this to http://127.0.0.1:8080/invoke (or your PORT)."
    );
  }
  return url;
}

/** SSE events as they arrive (skills → progress* → final). */
export async function* streamAgentcoreRuntimeEvents(params: {
  prompt: string;
  sessionId: string;
  signal?: AbortSignal;
}) {
  const body = await postAgentcoreRuntime(requireRuntimeUrl(), params);
  yield* iterateAgentcoreRuntimeEvents(body, params.signal);
}

export async function invokeAgentcoreRuntime(params: {
  prompt: string;
  sessionId: string;
  signal?: AbortSignal;
}) {
  const body = await postAgentcoreRuntime(requireRuntimeUrl(), params);
  return consumeAgentcoreRuntimeStream(body);
}
