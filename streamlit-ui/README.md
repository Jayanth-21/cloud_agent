# Streamlit UI for Cloud Intelligence Agent

Minimal UI: sidebar of chats, main area with messages + input. Calls **AgentCore Runtime directly** (boto3 `invoke_agent_runtime`); no streaming server. Same chat keeps the same session (conversation context in-memory on the runtime); new chat = new session.

## Setup

1. **Deploy the agent** (once): from repo root, `cd agent && agentcore deploy` (or use your agent name). Ensure `.bedrock_agentcore.yaml` exists with `agent_arn` and `default_agent`.

2. **AWS credentials**: Configure locally (e.g. `aws configure`) so the Streamlit app can call the runtime.

3. **Run Streamlit from repo root** (so `.bedrock_agentcore.yaml` is found):

   ```bash
   cd streamlit-ui
   pip install -r requirements.txt
   streamlit run app.py
   ```

   Or from repo root: `cd streamlit-ui && streamlit run app.py`. Optionally set `AGENT_CONFIG_PATH` to the path of `.bedrock_agentcore.yaml` if you run from another directory.

## Features

- **Sidebar**: "New chat" + list of chats (titles from first message). Stored in session state (lost on refresh).
- **Main**: Messages for the selected chat + chat input. Sends prompt with `session_id` = current chat id so the runtime keeps conversation context for that chat.
- **Streaming**: Progress then final answer from the runtime.
