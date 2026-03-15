# Phase 5: Custom UI (Vercel Chatbot + AgentCore Runtime)

This folder contains **modifications** to the [Vercel Chatbot](https://github.com/vercel/chatbot) template so the UI:

- Sends prompts to the **AgentCore Runtime** endpoint (your Phase 2 agent).
- Supports **streaming** responses when the runtime returns a stream.
- Displays **structured JSON** results (e.g. tool outputs, agent response).
- Supports **visualization schemas** for rendering charts/tables when the agent returns them.

**No backend (Lambda/agent) refactor**—only UI and a Next.js API route that proxies to AgentCore Runtime.

---

## Setup

1. **Clone the Vercel Chatbot** repo:
   ```bash
   git clone https://github.com/vercel/chatbot.git chatbot-app
   cd chatbot-app
   ```

2. **Copy this folder’s files** into the cloned app:
   - `app/api/agentcore/route.ts` → `chatbot-app/app/api/agentcore/route.ts`
   - `components/agent-json-result.tsx` → `chatbot-app/components/agent-json-result.tsx`
   - `lib/agentcore-client.ts` → `chatbot-app/lib/agentcore-client.ts`
   - Wire the chat UI to use the `/api/agentcore` route when using the Cloud Intelligence Agent (see Integration below).

3. **Environment variables** (e.g. `.env.local`):
   - `AGENTCORE_RUNTIME_INVOKE_URL` – HTTP URL of your AgentCore Runtime invoke endpoint (e.g. a Lambda or API Gateway that calls `InvokeAgentRuntime` and returns `text/event-stream` for streaming).
   - Optional: `AGENTCORE_AUTH_HEADER` (e.g. `Bearer <token>`) if your runtime requires it.

4. **Streaming and session (optional)**  
   - Send `stream: true` in the body to request a stream; the route forwards `Accept: text/event-stream` and streams the response when the backend returns SSE.  
   - Send `sessionId` (or `session_id`) to keep conversation context: use the same ID for a conversation so the runtime’s `runtimeSessionId` (and memory) is preserved.

5. **Install and run** the chatbot as usual (e.g. `pnpm install`, `pnpm dev`). Use the Cloud Intelligence Agent option to talk to your agent; responses and JSON will be shown in the chat and via the JSON/viz component.

**Note:** This repo’s Streamlit UI talks to the runtime directly (no HTTP proxy). For the Vercel Chatbot you need an HTTP endpoint; use Lambda, API Gateway, or your own proxy that calls `InvokeAgentRuntime` and streams the response.

---

## Integration

- **Chat input** → POST to your Next.js route (e.g. `/api/agentcore`) with `{ prompt, sessionId?, scope?, stream? }`.
- The **route** forwards to `AGENTCORE_RUNTIME_INVOKE_URL` with the same payload (or the format your runtime expects). If the runtime returns a stream, the route streams it back.
- **Display**: Use the existing message list for assistant text; use `AgentJsonResult` for messages that contain structured JSON or a visualization schema (e.g. `{ type: "json", data: {...} }` or `{ type: "visualization", schema: "...", data: {...} }`).

---

## Files in this folder

| File | Purpose |
|------|--------|
| `app/api/agentcore/route.ts` | Next.js API route: receives prompt/session, calls AgentCore Runtime, returns or streams response. |
| `components/agent-json-result.tsx` | Renders structured JSON and optional visualization schema (e.g. table/chart). |
| `lib/agentcore-client.ts` | Client helper to call `/api/agentcore` from the chat UI (optional). |

Adapt the chat page and model selector so that when “Cloud Intelligence Agent” (or your label) is selected, the app uses `/api/agentcore` and displays tool/result JSON with `AgentJsonResult` where appropriate.
