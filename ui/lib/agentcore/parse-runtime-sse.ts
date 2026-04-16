import type { CostChartSpec } from "./chart-spec";
import { isCostChartSpec } from "./chart-spec";

export type RuntimeSsePayload = {
  stage?: string;
  message?: string;
  result?: string;
  charts?: unknown[];
  clarification_needed?: boolean;
  skills?: unknown[];
  full_tools_fallback?: boolean;
};

function parseLine(line: string): RuntimeSsePayload | null {
  let s = line.trim();
  if (!s) return null;
  if (s.startsWith("data:")) s = s.slice(5).trim();
  if (!s || s === "[DONE]") return null;
  try {
    const obj = JSON.parse(s) as RuntimeSsePayload;
    return typeof obj === "object" && obj !== null ? obj : null;
  } catch {
    return null;
  }
}

function asCharts(raw: unknown): CostChartSpec[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter(isCostChartSpec);
}

export type ParsedAgentcoreRuntime = {
  result: string;
  charts: CostChartSpec[];
  clarificationNeeded: boolean;
};

export type RuntimeStreamEvent =
  | {
      kind: "skills";
      skills: string[];
      fullToolsFallback: boolean;
    }
  | { kind: "progress"; message: string }
  | {
      kind: "final";
      result: string;
      charts: CostChartSpec[];
      clarificationNeeded: boolean;
    };

function applyFinalPayload(
  obj: RuntimeSsePayload,
  acc: {
    lastResult: string;
    lastCharts: CostChartSpec[];
    clarificationNeeded: boolean;
  }
) {
  if (obj.clarification_needed === true) acc.clarificationNeeded = true;
  if (typeof obj.result === "string" && obj.result.trim()) {
    const t = obj.result.trim();
    if (t.length >= acc.lastResult.length) acc.lastResult = t;
  }
  const ch = asCharts(obj.charts);
  if (ch.length) acc.lastCharts = ch;
}

async function* readRuntimeSseLines(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal
): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const onAbort = () => {
    reader.cancel().catch(() => {});
  };
  if (signal) {
    if (signal.aborted) {
      onAbort();
      return;
    }
    signal.addEventListener("abort", onAbort, { once: true });
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        yield line;
      }
    }
    if (buffer.trim()) {
      yield buffer;
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
  }
}

/**
 * Incremental parse of AgentCore HTTP SSE (skills, progress, then final).
 */
export async function* iterateAgentcoreRuntimeEvents(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal
): AsyncGenerator<RuntimeStreamEvent> {
  const acc = {
    lastResult: "",
    lastCharts: [] as CostChartSpec[],
    clarificationNeeded: false,
  };

  for await (const line of readRuntimeSseLines(body, signal)) {
    const obj = parseLine(line);
    if (!obj) continue;

    if (obj.stage === "skills" && Array.isArray(obj.skills)) {
      const skills = obj.skills.filter((s): s is string => typeof s === "string");
      yield {
        kind: "skills",
        skills,
        fullToolsFallback: Boolean(obj.full_tools_fallback),
      };
      continue;
    }

    if (obj.stage === "progress" && typeof obj.message === "string") {
      yield { kind: "progress", message: obj.message };
      continue;
    }

    if (obj.stage === "error" && typeof obj.message === "string") {
      yield { kind: "progress", message: obj.message };
    }

    if ("result" in obj) {
      applyFinalPayload(obj, acc);
    }
  }

  yield {
    kind: "final",
    result: acc.lastResult,
    charts: acc.lastCharts,
    clarificationNeeded: acc.clarificationNeeded,
  };
}

/**
 * Consume AgentCore HTTP SSE body (same shape as `agent/src/main.py` yields).
 */
export async function consumeAgentcoreRuntimeStream(
  body: ReadableStream<Uint8Array>
): Promise<ParsedAgentcoreRuntime> {
  let last: ParsedAgentcoreRuntime = {
    result: "",
    charts: [],
    clarificationNeeded: false,
  };
  for await (const ev of iterateAgentcoreRuntimeEvents(body)) {
    if (ev.kind === "final") {
      last = {
        result: ev.result,
        charts: ev.charts,
        clarificationNeeded: ev.clarificationNeeded,
      };
    }
  }
  return last;
}
