# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Phase 2 Agent: LangGraph (Plan → Tool Selection → Execute → Evaluate → Iterate),
AgentCore Gateway (all tool calls), Bedrock inference, tool scoping.

Short-term memory: thread-scoped checkpoints. A single shared checkpointer is used
so that the same thread_id (session_id) loads prior conversation state within this process.
See: https://langchain-ai.github.io/langgraph/how-tos/persistence/
"""

import logging
import os
import sys
import uuid

logger = logging.getLogger(__name__)


def _configure_runtime_logging() -> None:
    """Ensure logs go to stdout so CloudWatch captures them in the container."""
    if os.environ.get("DOCKER_CONTAINER") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        root = logging.getLogger()
        if not root.handlers:
            h = logging.StreamHandler(sys.stdout)
            h.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
            root.addHandler(h)
            root.setLevel(logging.INFO)

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from bedrock_agentcore import BedrockAgentCoreApp

from graph.build import build_graph
from mcp_client.client import get_streamable_http_mcp_client
from model.load import load_model
from scoping.domains import filter_tools_by_domain, infer_domain_from_message
from tools.ask_user import get_ask_user_tool
from tools.visualization import get_visualization_tool

_configure_runtime_logging()
app = BedrockAgentCoreApp()
llm = load_model()

# Single checkpointer shared by all requests so thread_id (session_id) can load prior state.
# Required for multi-turn memory: same process must reuse this instance (Lambda: best-effort per container).
_checkpointer = InMemorySaver()


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
              "session_id" or "sessionId" (optional; used as thread_id for checkpointer) }
    Uses a shared checkpointer and thread_id for short-term memory; same session_id
    loads prior conversation in this process. Streams progress then final
    { "result", "messages", "clarification_needed" } as SSE.
    """
    prompt = payload.get("prompt", "What can you help me with?")
    scope = payload.get("scope") or infer_domain_from_message(prompt)
    session_id = (
        payload.get("session_id")
        or payload.get("sessionId")
        or os.environ.get("AGENTCORE_SESSION_ID")
        or str(uuid.uuid4())
    )

    logger.info("invoke start session_id=%s scope=%s prompt_len=%d", session_id, scope, len(prompt or ""))

    try:
        mcp_client = get_streamable_http_mcp_client()
        all_tools = await mcp_client.get_tools()
    except Exception as e:
        logger.exception("get_tools failed session_id=%s error=%s", session_id, e)
        print(f"[AGENT] get_tools failed session_id={session_id} error={e}", flush=True)
        raise
    tool_names = [getattr(t, "name", "?") for t in all_tools]
    logger.info("get_tools ok session_id=%s all_count=%d names=%s", session_id, len(all_tools), tool_names[:20])
    print(f"[AGENT] get_tools ok all_count={len(all_tools)} scope={scope} names={tool_names[:15]}", flush=True)
    scoped_tools = filter_tools_by_domain(all_tools, scope)
    scoped_names = [getattr(t, "name", "?") for t in scoped_tools]
    logger.info("scoped_tools scope=%s count=%d names=%s", scope, len(scoped_tools), scoped_names[:20])
    print(f"[AGENT] scoped_tools scope={scope} count={len(scoped_tools)} names={scoped_names[:15]}", flush=True)
    if len(scoped_tools) == 0 and scope == "cost":
        print("[AGENT] WARNING: no cost tools after scope filter; cost tools may be missing from Gateway", flush=True)
    scoped_tools = [get_ask_user_tool(), get_visualization_tool()] + list(scoped_tools)

    input_state = {
        "messages": [HumanMessage(content=prompt)],
        "scoped_tools": [],
        "results": [],
        "iteration": 0,
    }

    graph = build_graph(
        llm,
        max_iterations=10,
        checkpointer=_checkpointer,
        scoped_tools=scoped_tools,
    )
    config = {"configurable": {"thread_id": session_id}}
    result = None
    previous_state = None
    captured_answer = ""
    try:
        async for state in graph.astream(
            input_state, config=config, stream_mode="values"
        ):
            result = state
            msg = _progress_message(previous_state, state)
            if msg == "Formatting response...":
                captured_answer = _extract_last_content(state)
            previous_state = state
            yield {"stage": "progress", "message": msg or "Working..."}
    except Exception as e:
        logger.exception("invoke stream failed session_id=%s error=%s", session_id, e)
        raise

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
    logger.info(
        "invoke done session_id=%s result_len=%d clarification_needed=%s",
        session_id,
        len(last_content or ""),
        clarification_needed,
    )
    yield {"result": last_content, "messages": [], "clarification_needed": clarification_needed}


if __name__ == "__main__":
    app.run()
