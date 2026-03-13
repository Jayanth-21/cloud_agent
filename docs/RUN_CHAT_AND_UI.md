# How to run the agent, streaming server, and chat UI

Use these steps to run the Cloud Intelligence Agent and query it from the chat UI (with optional streaming).

---

## Prerequisites

- **Agent deployed**: Your AgentCore runtime (e.g. `cloudAgent`) is deployed and the Gateway is synced so tools (Cost, Logs, Audit) work. You should have a valid `.bedrock_agentcore.yaml` (e.g. under `agent/`) with `agent_arn` and `default_agent` set.
- **AWS credentials**: Configured (e.g. `aws configure` or env vars) so the streaming server and the deployed runtime can call AWS.
- **Node.js/pnpm**: For running the chat app (Vercel Chatbot with the `ui/` files copied in).

---

## Option A: UI with streaming server (recommended for local dev)

This uses the **streaming server** in this repo as the backend. The UI proxy forwards to it; the server calls `InvokeAgentRuntime` and streams the response (SSE) when the agent streams.

### 1. Start the streaming server

From the **repo root** (or from `agent/`):

```bash
# From repo root (config will be resolved from agent/.bedrock_agentcore.yaml)
python agent/streaming_server.py
```

Or from the `agent` directory:

```bash
cd agent
python streaming_server.py
```

You should see:

```
AgentCore streaming server at http://127.0.0.1:8080/invoke
```

Leave this terminal open.

### 2. Set up and run the chat UI

The `ui/` folder contains files to **copy into** the [Vercel Chatbot](https://github.com/vercel/chatbot) app.

1. **Clone the chatbot** (if you haven’t already):

   ```bash
   git clone https://github.com/vercel/chatbot.git chatbot-app
   cd chatbot-app
   ```

2. **Copy UI files** from this repo into the clone:

   - `ui/app/api/agentcore/route.ts` → `chatbot-app/app/api/agentcore/route.ts`
   - `ui/components/agent-json-result.tsx` → `chatbot-app/components/agent-json-result.tsx`
   - `ui/lib/agentcore-client.ts` → `chatbot-app/lib/agentcore-client.ts`

3. **Wire the chat** so that when “Cloud Intelligence Agent” (or your model label) is selected, the app:
   - POSTs to `/api/agentcore` with `{ prompt, sessionId?, scope?, stream: true }`
   - Uses `getAgentcoreStream()` for streaming or `sendToAgentcore()` for non-streaming
   - Renders assistant content and uses `AgentJsonResult` for JSON/tool results

4. **Environment variables** in the chatbot app (e.g. `.env.local`):

   ```env
   AGENTCORE_RUNTIME_INVOKE_URL=http://localhost:8080/invoke
   ```

   If the streaming server runs on another host/port, use that URL (e.g. `http://<host>:8080/invoke`).

5. **Install and run** the chatbot:

   ```bash
   pnpm install
   pnpm dev
   ```

6. **Use the chat** in the browser (e.g. http://localhost:3000):
   - Choose the “Cloud Intelligence Agent” (or your label) model.
   - Type a question, e.g. “What are my costs this month?” or “List cost tools.”
   - For the same conversation, keep the same **session ID** in the client so the agent keeps context (memory).

---

## Option B: UI pointing at a different backend (Lambda / API Gateway)

If you expose the AgentCore runtime via another URL (e.g. Lambda function URL or API Gateway that calls `InvokeAgentRuntime`):

1. Set **only**:

   ```env
   AGENTCORE_RUNTIME_INVOKE_URL=https://your-invoke-url/invoke
   ```

2. Ensure that backend:
   - Accepts POST with JSON body: `{ "prompt", "sessionId"?, "scope"? }`.
   - Uses `sessionId` as `runtimeSessionId` for the same conversation.
   - Returns either:
     - **Streaming**: `Content-Type: text/event-stream` and SSE body, or  
     - **Non-streaming**: `Content-Type: application/json` and a JSON body (e.g. `{ "result": "..." }`).

3. Run the chatbot as in Option A (steps 2–6), without running the repo’s streaming server.

---

## Option C: CLI only (no UI)

To invoke the agent from the command line (no streaming server, no UI):

```bash
cd agent
python invoke_agent.py "What are my costs this month?"
# Or with session and scope:
python invoke_agent.py '{"prompt": "Show cost by service", "scope": "cost", "session_id": "my-session-123"}'
```

Streaming vs JSON is determined by what the deployed agent returns; `invoke_agent.py` handles both (streaming via `iter_lines`, non-streaming via `iter_chunks` + JSON).

---

## Session and memory

- **Session ID**: Send the same `sessionId` (or `session_id`) on every request in a conversation. The runtime uses it as `runtimeSessionId`, so AgentCore Memory (if enabled) keeps context for that session.
- **Streaming**: The agent entrypoint is an async generator that streams progress events then the final `{ "result", "messages" }`. After you **redeploy** the agent, InvokeAgentRuntime will return `text/event-stream` and the streaming server will forward SSE to the UI when you use `stream: true`.

---

## Redeploying the agent (after code changes)

If you changed the agent (e.g. `agent/src/main.py`) and want streaming in the deployed runtime:

```bash
cd agent
agentcore deploy
# or with explicit agent name:
agentcore deploy --agent cloudAgent
```

Then use Option A or B as above; the streaming server (Option A) will stream responses when the runtime returns SSE.
