# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""LangGraph nodes: planner, tool_selection, execute, evaluate, loop_controller, generate_response."""

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from graph.state import AgentState

logger = logging.getLogger(__name__)


def _get_scoped_tools(state: AgentState, config: Any = None) -> list:
    """Get tool list from config (preferred, for checkpoint serialization) or state."""
    if config is not None:
        conf = config.get("configurable", {}) if isinstance(config, dict) else getattr(config, "configurable", {})
        tools = conf.get("scoped_tools") if isinstance(conf, dict) else getattr(conf, "scoped_tools", None)
        if tools is not None:
            return list(tools)
    return state.get("scoped_tools") or []


def _normalize_tool_name(name: str) -> str:
    """Normalize for matching: strip, lowercase, collapse spaces to single underscore."""
    if not name:
        return ""
    return "_".join((name or "").strip().lower().split()).replace(" ", "_")


def _resolve_tool_by_name(name: str, tools: list) -> BaseTool | None:
    """Resolve requested name to tool. Gateway uses TargetId___ToolName; LLM may return short name (e.g. get_cost_and_usage)."""
    if not name or not tools:
        return None
    name_clean = _normalize_tool_name(name)
    name_to_tool = {getattr(t, "name", ""): t for t in tools}
    t = name_to_tool.get(name)
    if t is not None:
        return t
    if name_clean:
        t = name_to_tool.get(name_clean)
        if t is not None:
            return t
    # Match by suffix after ___ (e.g. unified-aws-tools___get_cost_and_usage)
    for tool in tools:
        full = getattr(tool, "name", "")
        if not full:
            continue
        if "___" in full:
            suffix = full.split("___")[-1]
            if _normalize_tool_name(suffix) == name_clean or full.endswith("___" + name.strip()):
                return tool
        if _normalize_tool_name(full) == name_clean or full == name.strip():
            return tool
    return None


PLANNER_PROMPT = """You are a planning step for an AWS cloud intelligence agent. The user and conversation context are below.
Your job is to produce a short plan for the NEXT step only: what to do next (e.g. which tool to use and why, or conclude with a final answer).
If the user's request is vague or missing required details (e.g. cost query without date range, or without service/region), plan to call ask_user first to get the missing information. Do not call tools yourself. Output only the plan as plain text.

Current conversation:
{messages}

Previous plan (if any): {plan}
Previous evaluation (if any): {evaluation}

Output the next-step plan (one or two sentences):"""

TOOL_SELECTION_PROMPT = """Given the conversation and plan below, select which tool(s) to call and with what arguments.
Output a JSON object with one key "tool_calls" containing a list of objects, each with "name" (tool name) and "arguments" (dict of argument names to values).
Use the exact "name" from the Available tools list below (e.g. unified-aws-tools___get_cost_and_usage). If no tool is needed, output {{"tool_calls": []}}.

When the user specifies a time range (e.g. "last 7 days", "this month", "last 30 days"), call get_cost_and_usage (or get_today_date first if you need today for relative dates). Use the exact tool name from the list (e.g. unified-aws-tools___get_cost_and_usage) with appropriate time_period or start/end dates.

When to use the ask_user tool:
- Only when the user query is vague or missing required details (e.g. "what are my costs?" with no date range). When the user already said "last 7 days" or "this month", do NOT call ask_user; call get_cost_and_usage (or get_today_date then get_cost_and_usage) instead.

When to use the visualize_data tool:
- When you have time-series data (e.g. cost over time, forecast), call visualize_data with chart_type="line" and include_table=True.
- When you have categorical breakdown (e.g. cost by service, by region), call visualize_data with chart_type="bar" or chart_type="pie" and include_table=True.
- For cost or forecast queries, prefer returning both table and chart: set include_table=True. Pass the data from get_cost_and_usage or get_cost_forecast as the "data" argument (JSON string).
- Set value_label and category_label so the chart and table show clear units and dimensions: use value_label="Cost (USD)" for cost/forecast data (so axes show $); use value_label="Number of requests" (or similar) for request/count data; use category_label="Region", "Service", "Date", or "Day" as appropriate for what the categories represent.

Available tools (name and description):
{tool_descriptions}

Conversation:
{messages}

Plan: {plan}

Output only the JSON object, no markdown:"""

FINAL_RESPONSE_PROMPT = """Based on the conversation and tool results below, write a clear, concise final answer to the user. Do not call tools. Output only the answer.

Write a brief narrative summary of the cost/forecast findings (key numbers, insights, top drivers). Do NOT paste the visualize_data tool output or any base64 image in your text—the table and chart will be appended automatically so the user gets both your summary and the visualization.

Conversation and results:
{messages}

Available tools (for reference when user asks what tools they have):
{available_tools}

Tool results: {results}

Final answer to the user (narrative only; we will add the table/chart separately):"""

EVALUATE_PROMPT = """Given the conversation, plan, and tool results, decide what to do next.
Output exactly one word: DONE, CONTINUE, or RETRY.
- DONE: The task is complete; return this when you have enough information to answer the user.
- CONTINUE: More steps are needed; the agent will plan again (e.g. to add a table/chart).
- RETRY: The last step failed or was insufficient; the agent will try again with a different approach.

Important: If the LATEST tool result is from get_cost_and_usage or get_cost_forecast (or similar cost/forecast tools), and the tool "visualize_data" has NOT been run yet (check "Tools run so far" below), return CONTINUE so the agent can add a table and chart. Do not discard the cost/forecast information—it will be kept; the next step only adds visualization.

Tools run so far (by name): {tools_run_so_far}

Conversation (last few messages):
{messages}

Plan that was executed: {plan}

Tool results: {results}

Output only: DONE, CONTINUE, or RETRY"""


# Cap message and result size so prompts stay under Bedrock context limit (200k tokens).
# Limits are per item: each of the last N messages/results is truncated to the max chars.
_MSG_LAST_N = 5
_MSG_MAX_CHARS = 8000
_RESULT_MAX_CHARS = 20000
_RESULT_LAST_N = 8


def _trunc(s: str, max_chars: int) -> str:
    s = str(s)
    return s[:max_chars] + "..." if len(s) > max_chars else s


def _msg_preview(messages: list) -> str:
    lines = []
    for m in messages[-_MSG_LAST_N:]:
        raw = getattr(m, "content", str(m)) if hasattr(m, "content") else str(m)
        lines.append(_trunc(raw, _MSG_MAX_CHARS))
    return "\n".join(lines)


def _results_preview(results: list) -> str:
    """Truncate tool results for prompts to avoid token overflow."""
    if not results:
        return "(none)"
    out = []
    for r in results[-_RESULT_LAST_N:]:
        if not isinstance(r, dict):
            out.append(str(r)[:_RESULT_MAX_CHARS])
            continue
        name = r.get("name", "?")
        err = r.get("error")
        if err:
            out.append(f"[{name}] error: {_trunc(str(err), _RESULT_MAX_CHARS)}")
        else:
            out.append(f"[{name}] {_trunc(r.get('output', ''), _RESULT_MAX_CHARS)}")
    return "\n".join(out)


def _tool_args_for_description(tool: BaseTool) -> dict:
    """Get args schema properties dict; args_schema.schema can be a method (Pydantic), not a dict."""
    try:
        schema = getattr(tool.args_schema, "model_json_schema", None) or getattr(
            tool.args_schema, "schema", None
        )
        if callable(schema):
            schema = schema()
        if isinstance(schema, dict):
            return schema.get("properties", {})
    except Exception:
        pass
    return {}


def create_planner_node(llm: Any) -> Any:
    """Returns a planner node that uses the LLM to produce a next-step plan."""

    def planner(state: AgentState) -> dict:
        messages = state.get("messages", [])
        plan = state.get("plan", "")
        evaluation = state.get("evaluation", "")
        prompt = PLANNER_PROMPT.format(
            messages=_msg_preview(messages),
            plan=_trunc(plan or "(none)", 1200),
            evaluation=_trunc(evaluation or "(none)", 500),
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        content = response.content if hasattr(response, "content") else str(response)
        iteration = state.get("iteration", 0) + 1
        return {"plan": content.strip(), "iteration": iteration}

    return planner


def create_tool_selection_node(llm: Any, scoped_tools: list) -> Any:
    """Returns a tool_selection node that uses the LLM to choose tools and arguments. Tools from closure (not config)."""

    def tool_selection(state: AgentState, config: Any = None) -> dict:
        messages = state.get("messages", [])
        plan = state.get("plan", "")
        tools: list[BaseTool] = scoped_tools  # type: ignore
        tool_descriptions = "\n".join(
            f"- {t.name}: {t.description}; args: {_tool_args_for_description(t)}"
            for t in tools
        )
        prompt = TOOL_SELECTION_PROMPT.format(
            tool_descriptions=tool_descriptions or "(no tools)",
            messages=_msg_preview(messages),
            plan=_trunc(plan or "(none)", 1200),
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        content = response.content if hasattr(response, "content") else str(response)
        content = (content or "").strip()
        if "```" in content:
            content = content.split("```")[1].replace("json", "").strip()
        try:
            data = json.loads(content)
            tool_calls = data.get("tool_calls", [])
        except Exception as parse_err:
            logger.warning("tool_selection JSON parse failed: %s content_preview=%s", parse_err, (content[:300] if content else ""))
            print(f"[AGENT] tool_selection JSON parse failed: {parse_err}", flush=True)
            tool_calls = []
        selected = [
            {"name": (tc.get("name") or "").strip(), "arguments": tc.get("arguments") or {}}
            for tc in tool_calls
            if (tc.get("name") or "").strip()
        ]
        selected_names = [s["name"] for s in selected]
        logger.info("tool_selection selected count=%d names=%s", len(selected), selected_names)
        print(f"[AGENT] tool_selection selected count={len(selected)} names={selected_names}", flush=True)
        return {"selected_tools": selected}

    return tool_selection


def create_execute_node(scoped_tools: list) -> Any:
    """Returns an execute node that invokes selected tools via Gateway MCP. Tools from closure (not config)."""

    async def execute(state: AgentState, config: Any = None) -> dict:
        tools: list[BaseTool] = scoped_tools  # type: ignore
        selected = state.get("selected_tools", [])
        tool_names = [s.get("name", "") for s in selected if s.get("name")]
        logger.info("execute: running %d tool(s) %s", len(selected), tool_names)
        print(f"[AGENT] execute: running {len(selected)} tool(s) {tool_names}", flush=True)
        if len(selected) == 0:
            print("[AGENT] execute: no tools selected; Lambda will not be invoked", flush=True)
        results = []
        tool_messages = []
        for i, spec in enumerate(selected):
            name = spec.get("name", "")
            args = spec.get("arguments", {})
            t = _resolve_tool_by_name(name, tools)
            if not t:
                logger.warning("execute: %s -> Tool not found (not in scoped_tools)", name)
                results.append({"name": name, "error": "Tool not found"})
                tool_messages.append(
                    ToolMessage(content="Tool not found", tool_call_id=f"call_{i}")
                )
                continue
            try:
                out = await t.ainvoke(args)
                content = out if isinstance(out, str) else str(out)
                results.append({"name": name, "output": content})
                tool_messages.append(
                    ToolMessage(content=content, tool_call_id=f"call_{i}")
                )
                preview = (content[:200] + "…") if len(content) > 200 else content
                logger.info("execute: %s -> ok len=%d preview=%s", name, len(content), preview[:100] if preview else "(empty)")
            except Exception as e:
                logger.error("execute: %s -> error: %s", name, e)
                results.append({"name": name, "error": str(e)})
                tool_messages.append(
                    ToolMessage(content=str(e), tool_call_id=f"call_{i}")
                )
        return {"results": results, "messages": tool_messages}

    return execute


def create_evaluate_node(llm: Any) -> Any:
    """Returns an evaluate node that decides DONE / CONTINUE / RETRY."""

    def evaluate(state: AgentState) -> dict:
        messages = state.get("messages", [])
        plan = state.get("plan", "")
        results = state.get("results", [])
        tools_run_so_far = [r.get("name", "") for r in results if isinstance(r, dict)]
        prompt = EVALUATE_PROMPT.format(
            tools_run_so_far=", ".join(tools_run_so_far) or "(none)",
            messages=_msg_preview(messages),
            plan=_trunc(plan or "(none)", 1200),
            results=_results_preview(results),
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        content = response.content if hasattr(response, "content") else str(response)
        decision = (content or "").strip().upper()
        if "DONE" in decision:
            decision = "done"
        elif "RETRY" in decision:
            decision = "retry"
        else:
            decision = "continue"
        return {"evaluation": decision}

    return evaluate


def create_generate_response_node(llm: Any, scoped_tools: list) -> Any:
    """Produces final assistant message when evaluation is done. Tools from closure (not config)."""

    def generate_response(state: AgentState, config: Any = None) -> dict:
        messages = state.get("messages", [])
        results = state.get("results", [])
        available_tools = "\n".join(
            f"- {getattr(t, 'name', '?')}: {getattr(t, 'description', '') or 'No description'}"
            for t in scoped_tools
        ) if scoped_tools else "(none)"
        prompt = FINAL_RESPONSE_PROMPT.format(
            messages=_msg_preview(messages),
            available_tools=available_tools,
            results=_results_preview(results),
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        content = (response.content if hasattr(response, "content") else str(response)) or ""
        content = content.strip()
        # If ask_user was called, the reply should be that question only (clarification flow)
        from_ask_user = False
        for r in results:
            if not isinstance(r, dict):
                continue
            name = (r.get("name") or "").strip()
            if name == "ask_user" and "error" not in r:
                out = (r.get("output") or "").strip()
                if out:
                    content = out
                    from_ask_user = True
                break
        # Append visualize_data output so user gets narrative + table + chart (skip when replying with clarification)
        if not from_ask_user:
            for r in results:
                if not isinstance(r, dict):
                    continue
                name = (r.get("name") or "").strip()
                if name.endswith("visualize_data") or name == "visualize_data":
                    out = r.get("output", "")
                    if out and "error" not in r:
                        content = (content + "\n\n" + out).strip()
                    break
        if not content and results:
            parts = []
            for r in results:
                name = r.get("name", "?")
                if "error" in r:
                    parts.append(f"- **{name}**: Error: {r['error']}")
                else:
                    out = r.get("output", "")
                    # Keep visualize_data output intact so table + chart image render in the UI
                    if name == "visualize_data":
                        parts.append(out)
                    else:
                        parts.append(f"- **{name}**: {out[:2000]}{'...' if len(str(out)) > 2000 else ''}")
            content = "Here are the tool results:\n\n" + "\n\n".join(parts)
        if not content:
            content = "I couldn't generate a response. The model returned no text."
        return {"messages": [AIMessage(content=content)]}

    return generate_response


def create_loop_controller_node(max_iterations: int = 10) -> Any:
    """Returns a loop_controller node. Routing is done by the conditional edge; node must return a state dict."""

    def loop_controller(state: AgentState) -> dict:
        # LangGraph nodes must return a state update (dict). Routing to planner vs generate_response
        # is handled by the conditional edge _route_after_loop in build.py.
        return {}

    return loop_controller
