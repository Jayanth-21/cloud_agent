# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Phase 2 Agent: LangGraph (Plan → Tool Selection → Execute → Evaluate → Iterate),
AgentCore Gateway (all tool calls), Bedrock inference, tool scoping.
Session memory via LangGraph checkpointer (Postgres); each session_id is a thread_id.
"""

import logging
import os
import uuid

logger = logging.getLogger(__name__)

from langchain_core.messages import HumanMessage
from bedrock_agentcore import BedrockAgentCoreApp

from graph.build import build_graph
from mcp_client.client import get_streamable_http_mcp_client
from model.load import load_model
from scoping.domains import filter_tools_by_domain, infer_domain_from_message
from tools.ask_user import get_ask_user_tool
from tools.visualization import get_visualization_tool

app = BedrockAgentCoreApp()
llm = load_model()


def _progress_message(prev: dict | None, curr: dict) -> str | None:
    """Infer which graph node just ran from state diff; return human-readable progress message."""
    if prev is None:
        return "Starting..."
    prev_plan = prev.get("plan")
    curr_plan = curr.get("plan")
    if curr_plan is not None and curr_plan != prev_plan:
        return "Planning next step..."
    prev_sel = prev.get("selected_tools") or []
    curr_sel = curr.get("selected_tools") or []
    if curr_sel != prev_sel and curr_sel:
        names = [s.get("name") for s in curr_sel if s.get("name")]
        return f"Running tools: {', '.join(names)}..." if names else "Selecting tools..."
    prev_results = prev.get("results") or []
    curr_results = curr.get("results") or []
    if curr_results != prev_results and curr_results:
        names = [r.get("name", "?") for r in curr_results]
        return f"Querying {', '.join(names)}..."
    prev_eval = prev.get("evaluation")
    curr_eval = curr.get("evaluation")
    if curr_eval is not None and curr_eval != prev_eval:
        return "Evaluating results..."
    prev_msgs = prev.get("messages") or []
    curr_msgs = curr.get("messages") or []
    if len(curr_msgs) > len(prev_msgs) and curr_msgs:
        last_msg = curr_msgs[-1]
        if getattr(last_msg, "type", "") == "ai" or type(last_msg).__name__ == "AIMessage":
            return "Formatting response..."
    return None


def _message_content(m: object) -> str:
    """Get string content from a message (AIMessage or dict from serialized state)."""
    if m is None:
        return ""
    if isinstance(m, dict):
        content = m.get("content") or m.get("text")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                (str(block.get("text", block)) if isinstance(block, dict) else str(block))
                for block in content
            )
        return str(content) if content is not None else ""
    part = getattr(m, "content", None)
    if isinstance(part, str):
        return part
    if isinstance(part, list):
        return " ".join(
            (str(b.get("text", b)) if isinstance(b, dict) else str(b)) for b in part
        )
    return str(part) if part is not None else ""


def _is_ai_message(m: object) -> bool:
    """True if message is from the assistant (AIMessage or dict with type 'ai')."""
    if isinstance(m, dict):
        return (m.get("type") or m.get("type_")) == "ai"
    return getattr(m, "type", "") == "ai" or type(m).__name__ == "AIMessage"


def _extract_last_content(result: dict) -> str:
    """Extract final assistant text from graph result (full state when stream_mode='values')."""
    out_messages = result.get("messages", []) or []
    last_content = ""
    for m in reversed(out_messages):
        if _is_ai_message(m):
            last_content = _message_content(m)
            if last_content.strip():
                break
    if not last_content.strip() and out_messages:
        last_content = _message_content(out_messages[-1])
    return (last_content or "").strip()


@app.entrypoint
async def invoke(payload: dict):
    """
    Payload: { "prompt": "<user input>", "scope": "cost"|"logs"|"audit"|"all" (optional),
              "session_id" or "sessionId" (optional; used as thread_id for checkpointer memory) }
    Session memory is persisted via LangGraph checkpointer (Postgres when CHECKPOINT_POSTGRES_URI is set).
    Each session_id is one conversation thread; same session_id loads prior state from Postgres.
    Streams progress events (stage) then final { "result", "messages" } as SSE when
    used via InvokeAgentRuntime; clients get text/event-stream.
    """
    prompt = payload.get("prompt", "What can you help me with?")
    scope = payload.get("scope") or infer_domain_from_message(prompt)
    session_id = (
        payload.get("session_id")
        or payload.get("sessionId")
        or os.environ.get("AGENTCORE_SESSION_ID")
        or str(uuid.uuid4())
    )

    logger.info("invoke payload_keys=%s session_id=%s", list(payload.keys()), session_id)

    mcp_client = get_streamable_http_mcp_client()
    all_tools = await mcp_client.get_tools()
    scoped_tools = filter_tools_by_domain(all_tools, scope)
    scoped_tools = [get_ask_user_tool(), get_visualization_tool()] + list(scoped_tools)

    # Input for this turn: new message + scoped_tools; results/iteration reset for new turn (checkpointer merges).
    input_state = {
        "messages": [HumanMessage(content=prompt)],
        "scoped_tools": scoped_tools,
        "results": [],
        "iteration": 0,
    }

    # Default local Postgres for session memory; override with CHECKPOINT_POSTGRES_URI in production.
    _DEFAULT_POSTGRES_URI = "postgresql://postgres:password@localhost:5432/cloud_agent?sslmode=disable"
    postgres_uri = (os.environ.get("CHECKPOINT_POSTGRES_URI") or _DEFAULT_POSTGRES_URI).strip()
    if postgres_uri:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(postgres_uri) as checkpointer:
            await checkpointer.setup()
            graph = build_graph(llm, max_iterations=10, checkpointer=checkpointer)
            config = {"configurable": {"thread_id": session_id}}
            result = None
            previous_state = None
            captured_answer = ""
            async for state in graph.astream(
                input_state, config=config, stream_mode="values"
            ):
                result = state
                msg = _progress_message(previous_state, state)
                if msg == "Formatting response...":
                    captured_answer = _extract_last_content(state)
                previous_state = state
                yield {"stage": "progress", "message": msg or "Working..."}
    else:
        logger.warning(
            "CHECKPOINT_POSTGRES_URI not set; running without persistence (single-turn only)"
        )
        graph = build_graph(llm, max_iterations=10)
        result = None
        previous_state = None
        captured_answer = ""
        async for state in graph.astream(input_state, stream_mode="values"):
            result = state
            msg = _progress_message(previous_state, state)
            if msg == "Formatting response...":
                captured_answer = _extract_last_content(state)
            previous_state = state
            yield {"stage": "progress", "message": msg or "Working..."}

    last_content = captured_answer.strip() or _extract_last_content(result or {})
    if not last_content:
        results = (result or {}).get("results") or []
        if results:
            parts = []
            for r in results:
                name = r.get("name", "?")
                if "error" in r:
                    parts.append(f"- **{name}**: Error: {r['error']}")
                else:
                    out = r.get("output", "")
                    parts.append(f"- **{name}**: {str(out)[:2000]}{'...' if len(str(out)) > 2000 else ''}")
            last_content = "Here are the tool results:\n\n" + "\n\n".join(parts)
    if not last_content:
        last_content = (
            "No response generated. The agent completed but returned no text. "
            "You may want to retry or check model/credentials."
        )
    clarification_needed = any(
        isinstance(r, dict) and (r.get("name") or "").strip() == "ask_user"
        for r in (result or {}).get("results") or []
    )
    yield {"result": last_content, "messages": [], "clarification_needed": clarification_needed}


if __name__ == "__main__":
    app.run()
