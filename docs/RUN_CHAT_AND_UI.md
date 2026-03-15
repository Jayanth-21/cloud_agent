# How to run the agent and chat UI

Use these steps to run the Cloud Intelligence Agent and query it from the chat UI.

---

## Prerequisites

- **Agent deployed**: Your AgentCore runtime is deployed. You should have a valid `.bedrock_agentcore.yaml` (e.g. at repo root or under `agent/`) with `agent_arn` and `default_agent` set.
- **AWS credentials**: Configured (e.g. `aws configure` or env vars) so the Streamlit app and the deployed runtime can call AWS.

---

## Streamlit UI (recommended for local dev)

The Streamlit app calls the **AgentCore Runtime directly** (no streaming server). No HTTP proxy; boto3 `invoke_agent_runtime` from the Streamlit process.

1. **Run Streamlit from repo root** (so `.bedrock_agentcore.yaml` is found):

   ```bash
   cd streamlit-ui
   pip install -r requirements.txt
   streamlit run app.py
   ```

2. Open the URL shown (e.g. http://localhost:8501). Use the same chat for one conversation (same session); start a "New chat" for a different conversation (new session). Session memory is in-memory on the runtime.

3. Optional: set `AGENT_CONFIG_PATH` to the path of your `.bedrock_agentcore.yaml` if you run from a directory where the config is not auto-discovered.

---

## Vercel Chatbot (optional)

The `ui/` folder contains files to **copy into** the [Vercel Chatbot](https://github.com/vercel/chatbot) app. You need a backend that accepts POST with `{ "prompt", "sessionId", "scope"? }` and returns SSE or JSON. Options:

- **Lambda / API Gateway**: Expose an endpoint that calls `InvokeAgentRuntime` and set `AGENTCORE_RUNTIME_INVOKE_URL` to that URL.
- **Local proxy**: Run a small HTTP server that reads `.bedrock_agentcore.yaml`, calls `invoke_agent_runtime`, and streams the response; point the chatbot at `http://localhost:8080/invoke` (this repo no longer includes that server; you can add one if needed).

---

## CLI only (no UI)

To invoke the agent from the command line:

```bash
cd agent
python invoke_agent.py "What are my costs this month?"
# Or with session and scope:
python invoke_agent.py '{"prompt": "Show cost by service", "scope": "cost", "session_id": "my-session-123"}'
```

---

## Session and memory

- **Session ID**: The same `session_id` (or `sessionId`) keeps one conversation. The runtime uses it as LangGraph `thread_id` with a **shared** in-memory checkpointer so prior turns are loaded. New chat = new session ID = separate conversation.
- **Streaming**: The agent streams progress events then the final `{ "result", "clarification_needed" }`. Streamlit consumes the stream directly from the runtime.
- **Lambda**: Session memory is process-scoped; if the same container serves both messages, memory works. For durable memory across all Lambda invocations, use a persistent checkpointer (e.g. DynamoDB).

---

## Redeploying the agent

After code changes to the agent (e.g. `agent/src/main.py`):

```bash
cd agent
agentcore deploy
```

Then run Streamlit again; it will talk to the updated runtime.
