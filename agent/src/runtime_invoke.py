# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Shared LangGraph invoke loop: progress + final result dicts (same payloads as SSE events).
Used by local HTTP server and optionally by BedrockAgentCoreApp entrypoint.
"""

from pathlib import Path

from dotenv import load_dotenv

# agent/src/runtime_invoke.py -> agent/.env (gitignored; copy from .env.example)
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import logging
import os
import re
import sys
import uuid
from collections.abc import AsyncIterator

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from graph.build import build_graph
from mcp_client.client import get_streamable_http_mcp_client
from model.load import load_model
from skills.router import route_skills_for_prompt
from skills.tool_filter import filter_tools_by_allowlist
from tools.ask_user import get_ask_user_tool
from tools.visualization import (
    build_auto_viz_from_conversation,
    build_auto_viz_from_results,
    get_visualization_tool,
)
from viz_pipeline.cost_chart_spec import build_cost_chart_specs_from_results

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    if os.environ.get("DOCKER_CONTAINER") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        root = logging.getLogger()
        if not root.handlers:
            h = logging.StreamHandler(sys.stdout)
            h.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
            root.addHandler(h)
            root.setLevel(logging.INFO)


_configure_logging()

llm = load_model()

_memory_checkpointer = InMemorySaver()
_cached_checkpointer = None
_cached_checkpoint_key: str | None = None


def _resolve_checkpointer():
    """Optional on-disk checkpoints for local dev (LANGGRAPH_CHECKPOINT_SQLITE=file path)."""
    global _cached_checkpointer, _cached_checkpoint_key
    path = (os.environ.get("LANGGRAPH_CHECKPOINT_SQLITE") or "").strip()
    key = path or "__memory__"
    if _cached_checkpointer is not None and _cached_checkpoint_key == key:
        return _cached_checkpointer
    if not path:
        _cached_checkpointer = _memory_checkpointer
        _cached_checkpoint_key = key
        return _cached_checkpointer
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        logger.warning("LANGGRAPH_CHECKPOINT_SQLITE set but SqliteSaver unavailable; using memory")
        _cached_checkpointer = _memory_checkpointer
        _cached_checkpoint_key = "__memory__"
        return _cached_checkpointer
    abs_path = path if os.path.isabs(path) else os.path.abspath(path)
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        url_path = abs_path.replace("\\", "/")
        cp = SqliteSaver.from_conn_string(f"sqlite:///{url_path}")
        _cached_checkpointer = cp
        _cached_checkpoint_key = key
        return cp
    except Exception:
        logger.exception("SqliteSaver failed; using in-memory checkpointer")
        _cached_checkpointer = _memory_checkpointer
        _cached_checkpoint_key = "__memory__"
        return _cached_checkpointer


def _progress_message(prev: dict | None, curr: dict) -> str | None:
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
    if isinstance(m, dict):
        return (m.get("type") or m.get("type_")) == "ai"
    return getattr(m, "type", "") == "ai" or type(m).__name__ == "AIMessage"


def _extract_last_content(result: dict) -> str:
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


async def stream_agent_events(payload: dict) -> AsyncIterator[dict]:
    """
    Yields progress dicts then a final dict with result, charts, clarification_needed.
    Same contract as the former AgentCore-only main.invoke.
    """
    prompt = payload.get("prompt", "What can you help me with?")
    session_id = (
        payload.get("session_id")
        or payload.get("sessionId")
        or os.environ.get("AGENTCORE_SESSION_ID")
        or str(uuid.uuid4())
    )

    logger.info("invoke start session_id=%s prompt_len=%d", session_id, len(prompt or ""))

    try:
        mcp_client = get_streamable_http_mcp_client()
        all_tools = await mcp_client.get_tools()
    except Exception as e:
        logger.exception("get_tools failed session_id=%s error=%s", session_id, e)
        print(f"[AGENT] get_tools failed session_id={session_id} error={e}", flush=True)
        raise

    tool_names = [getattr(t, "name", "?") for t in all_tools]
    logger.info("get_tools ok session_id=%s all_count=%d names=%s", session_id, len(all_tools), tool_names[:20])
    print(f"[AGENT] get_tools ok all_count={len(all_tools)} names={tool_names[:15]}", flush=True)

    route = route_skills_for_prompt(prompt or "")
    logger.info(
        "skill_route session_id=%s selected=%s full_tools_fallback=%s",
        session_id,
        route.selected_ids,
        route.used_full_tool_fallback,
    )
    mcp_tools = filter_tools_by_allowlist(all_tools, route.allowed_tool_bases)
    scoped_names = [getattr(t, "name", "?") for t in mcp_tools]
    logger.info("scoped_tools count=%d names=%s", len(mcp_tools), scoped_names[:25])
    print(
        f"[AGENT] scoped_tools count={len(mcp_tools)} skills={route.selected_ids} "
        f"fallback_all={route.used_full_tool_fallback}",
        flush=True,
    )
    yield {
        "stage": "skills",
        "skills": list(route.selected_ids),
        "full_tools_fallback": route.used_full_tool_fallback,
    }
    scoped_tools = [get_ask_user_tool(), get_visualization_tool()] + list(mcp_tools)

    input_state = {
        "messages": [HumanMessage(content=prompt)],
        "scoped_tools": [],
        "results": [],
        "iteration": 0,
    }

    checkpointer = _resolve_checkpointer()
    graph = build_graph(
        llm,
        max_iterations=20,
        checkpointer=checkpointer,
        scoped_tools=scoped_tools,
        skill_context=route.context_markdown,
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

    last_content = (_extract_last_content(result or {}) or "").strip()
    if not last_content:
        last_content = captured_answer.strip()
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
    results_final = (result or {}).get("results") or []
    chart_specs = build_cost_chart_specs_from_results(results_final, prompt)
    if chart_specs:
        last_content = re.sub(
            r"!\[[^\]]*\]\(data:image/png;base64,[^\)]+\)\s*",
            "",
            last_content or "",
        ).strip()
    if last_content and "data:image/png;base64" not in last_content and not chart_specs:
        try:
            viz_md = build_auto_viz_from_results(results_final, user_query=prompt)
            if not viz_md:
                viz_md = build_auto_viz_from_conversation(
                    (result or {}).get("messages") or [], user_query=prompt
                )
            if viz_md:
                last_content = f"{last_content.rstrip()}\n\n{viz_md}"
                logger.info("invoke: appended chart markdown chars=%d", len(viz_md))
        except Exception:
            logger.exception("invoke: chart append failed")

    clarification_needed = any(
        isinstance(r, dict) and (r.get("name") or "").strip() == "ask_user"
        for r in (result or {}).get("results") or []
    )
    has_chart = "data:image/png;base64" in (last_content or "")
    logger.info(
        "invoke done session_id=%s result_len=%d clarification_needed=%s has_png_chart=%s chart_specs=%d",
        session_id,
        len(last_content or ""),
        clarification_needed,
        has_chart,
        len(chart_specs),
    )
    print(
        f"[AGENT] invoke done result_len={len(last_content or '')} has_png_chart={has_chart} chart_specs={len(chart_specs)}",
        flush=True,
    )
    yield {
        "result": last_content,
        "messages": [],
        "clarification_needed": clarification_needed,
        "charts": chart_specs,
        "chartVersion": "cloud_intel_charts/v1" if chart_specs else None,
    }
