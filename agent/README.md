# Cloud Intelligence Agent (Phase 2)

Agent layer: LangGraph, AgentCore Gateway (MCP → unified_tools Lambda), Bedrock chat. **Skills** are injected into the graph prompts only (quick reference + full `SKILL.md` playbooks)—no separate router LLM call. All MCP tools stay available; the planner / tool-selection steps choose what to run. Session memory uses LangGraph’s checkpointer (in-memory by default; optional SQLite on disk for local dev).

## Layout

- **skills/** – Agent Skills packages (`*/SKILL.md`): YAML frontmatter (`id`, `description`, `tools` allowlist, `routing_body_chars`) + playbook markdown.
- **src/skills/** – Load skills, build **prompt injection** (discovery + playbooks); **no tool filtering** (full MCP surface; YAML `tools` lists are hints).
- **src/main.py** – Local laptop: Starlette + uvicorn; AgentCore container: `BedrockAgentCoreApp` when `DOCKER_CONTAINER=1` or `CLOUD_AGENT_AGENTCORE=1`.
- **src/runtime_invoke.py** – Invoke loop; calls skill router; builds graph with `skill_context` injected into prompts.
- **src/local_http.py** – `POST /invoke` (SSE), `GET /health`.
- **src/graph/** – LangGraph state, nodes, build.
- **src/mcp_client/** – MCP client to Gateway URL.
- **src/model/** – ChatBedrock.

## Skills in the graph

Every `SKILL.md` under `skills/` is loaded: a **quick reference** (id, name, description) plus the **full playbook** is prepended to planner / tool selection / evaluate / final-answer prompts. There is **no** extra Bedrock call before the graph.

| Variable | Default | Meaning |
|----------|---------|---------|
| `SKILLS_DIR` | *(agent root)*`/skills` | Override skills folder |

## Run locally (laptop)

AWS credentials (`aws configure` or SSO) are used for **Bedrock** (chat) and **Gateway → Lambda**.

1. Copy **`agent/.env.example`** → **`agent/.env`** and set at least **`AWS_REGION`** (same region as in the Bedrock console). Variables are loaded automatically via **`python-dotenv`** when the agent starts (see `runtime_invoke.py`).
2. In **AWS Console → Amazon Bedrock → Model access**, enable **serverless** access for your **Claude** chat model (used for the graph and for skill routing).
3. Run:

```bash
cd agent
pip install -e .
set PYTHONPATH=src
python src/main.py
```

Defaults: **http://127.0.0.1:8080/invoke**. In **`ui/.env.local`**: `AGENTCORE_RUNTIME_INVOKE_URL=http://127.0.0.1:8080/invoke`

Optional: **`LANGGRAPH_CHECKPOINT_SQLITE`**, **`GATEWAY_MCP_URL`**.

To run **BedrockAgentCoreApp** on the laptop instead, set `CLOUD_AGENT_AGENTCORE=1` before `python src/main.py`.

## Run the chat UI (`ui/`)

Postgres + `AUTH_SECRET` + `AGENTCORE_RUNTIME_INVOKE_URL`. Skill routing runs **only in the Python agent**; the UI does not pick skills.

## Deploy to AgentCore Runtime

Use `.bedrock_agentcore.yaml`. Ensure the container image includes the **`skills/`** directory next to **`src/`** (same layout as local). The runtime role needs **`bedrock:InvokeModel`** on your chat model.

## Payload (HTTP POST `/invoke`)

- **prompt** (required)
- **session_id** or **sessionId** (optional): LangGraph **thread_id** for multi-turn

## Session memory

In-memory per process, or durable with **`LANGGRAPH_CHECKPOINT_SQLITE`** on one machine.
