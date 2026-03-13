/**
 * Phase 5: Proxy to AgentCore Runtime.
 * Sends prompt to AGENTCORE_RUNTIME_INVOKE_URL; supports streaming when runtime returns stream.
 * No backend refactor—UI only.
 */

import { NextRequest, NextResponse } from "next/server";

const RUNTIME_URL = process.env.AGENTCORE_RUNTIME_INVOKE_URL;
const AUTH_HEADER = process.env.AGENTCORE_AUTH_HEADER; // e.g. "Bearer <token>"

export async function POST(req: NextRequest) {
  if (!RUNTIME_URL) {
    return NextResponse.json(
      { error: "AGENTCORE_RUNTIME_INVOKE_URL not set" },
      { status: 500 }
    );
  }

  let body: { prompt?: string; sessionId?: string; scope?: string; stream?: boolean };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const prompt = body.prompt ?? "";
  const sessionId = body.sessionId ?? "";
  const scope = body.scope ?? "";
  const stream = body.stream === true;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (AUTH_HEADER) headers["Authorization"] = AUTH_HEADER;
  if (stream) headers["Accept"] = "text/event-stream";

  const payload = {
    prompt,
    ...(sessionId && { sessionId, session_id: sessionId }),
    ...(scope && { scope }),
  };

  try {
    const res = await fetch(RUNTIME_URL, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      ...(stream && { signal: req.signal }),
    });

    if (!res.ok) {
      const text = await res.text();
      return NextResponse.json(
        { error: `Runtime error: ${res.status}`, details: text },
        { status: res.status }
      );
    }

    const contentType = res.headers.get("content-type") ?? "";
    if (stream && contentType.includes("text/event-stream")) {
      return new Response(res.body, {
        headers: { "Content-Type": "text/event-stream" },
      });
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch (e) {
    const message = e instanceof Error ? e.message : "Unknown error";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
