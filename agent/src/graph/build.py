# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build LangGraph: Plan → Tool Selection → Execute → Evaluate → Iterate."""

from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from graph.state import AgentState
from graph.nodes import (
    create_evaluate_node,
    create_execute_node,
    create_generate_response_node,
    create_loop_controller_node,
    create_planner_node,
    create_prepare_viz_node,
    create_tool_selection_node,
)


def build_graph(
    llm: BaseChatModel,
    max_iterations: int = 20,
    checkpointer: Optional[Any] = None,
    scoped_tools: Optional[list] = None,
    skill_context: str = "",
) -> CompiledStateGraph[AgentState]:
    """
    Build the agent graph. Scoped tools are passed via closure (not config) so the
    checkpointer does not serialize them. skill_context is injected into LLM prompts
    (SKILL.md playbooks from semantic routing).
    """
    tools_list = list(scoped_tools) if scoped_tools else []
    builder = StateGraph(AgentState)

    builder.add_node("planner", create_planner_node(llm, skill_context=skill_context))
    builder.add_node(
        "tool_selection", create_tool_selection_node(llm, tools_list, skill_context=skill_context)
    )
    builder.add_node("execute", create_execute_node(tools_list))
    builder.add_node("evaluate", create_evaluate_node(llm, skill_context=skill_context))
    builder.add_node("loop_controller", create_loop_controller_node(max_iterations))
    builder.add_node("prepare_viz", create_prepare_viz_node(llm))
    builder.add_node(
        "generate_response",
        create_generate_response_node(llm, tools_list, skill_context=skill_context),
    )

    builder.add_edge("__start__", "planner")
    builder.add_edge("planner", "tool_selection")
    builder.add_edge("tool_selection", "execute")
    builder.add_edge("execute", "evaluate")
    builder.add_edge("evaluate", "loop_controller")
    builder.add_conditional_edges(
        "loop_controller", _make_route_after_loop(max_iterations)
    )
    builder.add_edge("prepare_viz", "generate_response")
    builder.add_edge("generate_response", "__end__")

    return builder.compile(checkpointer=checkpointer)


def _make_route_after_loop(max_iterations: int):
    """Router respects build_graph(max_iterations)."""

    def _route_after_loop(state: AgentState) -> str:
        evaluation = state.get("evaluation", "")
        iteration = state.get("iteration", 0)
        if evaluation == "done" or iteration >= max_iterations:
            return "prepare_viz"
        return "planner"

    return _route_after_loop
