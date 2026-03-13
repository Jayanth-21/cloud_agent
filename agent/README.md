# Cloud Intelligence Agent (Phase 2)

Agent layer for the Cloud Intelligence project: LangGraph orchestration, AgentCore Gateway (all tool calls), Bedrock inference, tool scoping. Session memory is handled by LangGraph checkpointer (Postgres), not AgentCore Memory.

## Layout

- **src/main.py** – Entrypoint (BedrockAgentCoreApp); builds graph, loads Gateway tools, scopes by domain, invokes with optional Postgres checkpointer.
- **src/graph/** – LangGraph state, nodes (planner, tool_selection, execute, evaluate, loop_controller, generate_response), build.
- **src/scoping/** – Cost / Logs / Audit domains; `filter_tools_by_domain()`, `infer_domain_from_message()`.
- **src/mcp_client/** – MCP client to AgentCore Gateway URL (no direct AWS APIs).
- **src/model/** – ChatBedrock (Bedrock inference).

## Run

Set **GATEWAY_MCP_URL** to your AgentCore Gateway MCP endpoint (e.g. `https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp`).

For **multi-turn conversation memory**, the agent uses **CHECKPOINT_POSTGRES_URI** if set; otherwise it defaults to `postgresql://postgres:password@localhost:5432/cloud_agent?sslmode=disable` (local Postgres DB `cloud_agent`). Override the env var for production or different credentials.

From project root:

```bash
cd agent
pip install -e .
PYTHONPATH=src python src/main.py
```

Or deploy to AgentCore Runtime using `.bedrock_agentcore.yaml` (point entrypoint and source_path to this agent).

## Session memory (Postgres checkpointer)

- By default the agent uses `postgresql://postgres:password@localhost:5432/cloud_agent?sslmode=disable`. Set **CHECKPOINT_POSTGRES_URI** to override (e.g. different host, user, or DB).
- Use a **local Postgres** instance with a database named `cloud_agent` (or create one), or point the URI to your own DB.
- On first use the checkpointer calls `setup()` and creates the required tables (checkpoints, checkpoint_blobs, etc.).
- Each **session_id** (or **sessionId**) in the payload is used as the LangGraph **thread_id**; the same ID loads/saves conversation state for that session. New session = new ID = separate conversation.

## Payload

- **prompt** (required): User message.
- **scope** (optional): `"cost"` | `"logs"` | `"audit"` | `"all"`. Inferred from prompt if omitted.
- **session_id** or **sessionId** (optional): Conversation thread; same ID keeps multi-turn context (when CHECKPOINT_POSTGRES_URI is set).
