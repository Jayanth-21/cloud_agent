# Streamlit UI for Cloud Intelligence Agent

Minimal UI: sidebar of chats, main area with messages + input. One API call to your streaming server with `sessionId` (chat id); response is streamed back. No Postgres/Redis/drizzle; add auth when you want it.

Kept in **streamlit-ui** at repo root, separate from chatbot-app.

## Setup

1. **Streaming server** (from repo root):

   ```bash
   python agent/streaming_server.py
   ```
   Serves `http://127.0.0.1:8080/invoke` by default.

2. **Streamlit app** (from repo root):

   ```bash
   cd streamlit-ui
   pip install -r requirements.txt
   streamlit run app.py
   ```

3. Optional: set `STREAMING_SERVER_URL` if the server is not at `http://127.0.0.1:8080/invoke`:

   ```bash
   export STREAMING_SERVER_URL=http://localhost:8080/invoke
   streamlit run app.py
   ```
   Or add to `.streamlit/secrets.toml`: `STREAMING_SERVER_URL = "http://..."`

## If you get "Read timed out"

Use **direct Bedrock** (no streaming server, same path as `invoke_agent.py`):

```bash
cd streamlit-ui
pip install -r requirements.txt
set USE_DIRECT_BEDROCK=1
streamlit run app.py
```

Run from the **repo root** or set `AGENT_CONFIG_PATH` to the path of your `.bedrock_agentcore.yaml`. AWS credentials must be available (env or `~/.aws/credentials`).

## Features

- **Sidebar**: "New chat" + list of chats (titles from first message). Stored in session state (lost on refresh); can be replaced with DB later.
- **Main**: Messages for the selected chat + chat input. Sends prompt with `sessionId` = current chat id so the agent keeps conversation context.
- **Streaming**: Progress messages then the final answer. With `USE_DIRECT_BEDROCK=1`, the app calls Bedrock directly (no HTTP timeout).
