# Cloud Intelligence Agent (Phase 2)

Agent layer for the Cloud Intelligence project: LangGraph orchestration, AgentCore Gateway (all tool calls), Bedrock inference, tool scoping. Session memory is handled by LangGraph in-memory checkpointer (InMemorySaver), not AgentCore Memory.

## Layout

- **src/main.py** – Entrypoint (BedrockAgentCoreApp); builds graph, loads Gateway tools, scopes by domain, invokes with in-memory checkpointer.
- **src/graph/** – LangGraph state, nodes (planner, tool_selection, execute, evaluate, loop_controller, generate_response), build.
- **src/scoping/** – Cost / Logs / Audit domains; `filter_tools_by_domain()`, `infer_domain_from_message()`.
- **src/mcp_client/** – MCP client to AgentCore Gateway URL (no direct AWS APIs).
- **src/model/** – ChatBedrock (Bedrock inference).

## Run

**GATEWAY_MCP_URL** is optional; it defaults to the project's Gateway MCP endpoint. Set it only to override (e.g. a different gateway or region).

**Multi-turn conversation memory** is provided by LangGraph’s in-memory checkpointer (InMemorySaver). Session state lives in the runtime process only; use the same **session_id** (or **sessionId**) in the payload to keep context for a conversation.

### Run the chat UI (Streamlit)

Streamlit calls the AgentCore Runtime directly (no streaming server). From **repo root**, with AWS credentials configured (e.g. `aws configure`):

```bash
cd streamlit-ui
pip install -r requirements.txt
streamlit run app.py
```

Ensure `.bedrock_agentcore.yaml` is at repo root (or set `AGENT_CONFIG_PATH`). Same chat = same session; new chat = new session (in-memory on the runtime).

### Run agent entrypoint directly

```bash
cd agent
pip install -e .
PYTHONPATH=src python src/main.py
```

Or deploy to AgentCore Runtime using `.bedrock_agentcore.yaml` (point entrypoint and source_path to this agent).

## Session memory (in-memory checkpointer)

- The agent uses a **single shared** LangGraph **InMemorySaver** so that the same **session_id** loads prior conversation state (multi-turn memory).
- Each **session_id** (or **sessionId**) in the payload is used as the LangGraph **thread_id**; the same ID keeps conversation context for that session. New session = new ID = separate conversation.
- Memory is process-scoped: it works when the same runtime process serves both turns (e.g. long-lived container). On Lambda, session memory is best-effort when the same container is reused; for durable cross-invocation memory, use a persistent checkpointer (e.g. DynamoDB).

## Payload

- **prompt** (required): User message.
- **scope** (optional): `"cost"` | `"logs"` | `"audit"` | `"all"`. Inferred from prompt if omitted.
- **session_id** or **sessionId** (optional): Conversation thread; same ID keeps multi-turn context (in-memory on the runtime).
