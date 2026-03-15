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
    create_tool_selection_node,
)


def build_graph(
    llm: BaseChatModel,
    max_iterations: int = 10,
    checkpointer: Optional[Any] = None,
    scoped_tools: Optional[list] = None,
) -> CompiledStateGraph[AgentState]:
    """
    Build the agent graph. Scoped tools are passed via closure (not config) so the
    checkpointer does not serialize them. If checkpointer is provided, state is
    persisted by thread_id for multi-turn.
    """
    tools_list = list(scoped_tools) if scoped_tools else []
    builder = StateGraph(AgentState)

    builder.add_node("planner", create_planner_node(llm))
    builder.add_node("tool_selection", create_tool_selection_node(llm, tools_list))
    builder.add_node("execute", create_execute_node(tools_list))
    builder.add_node("evaluate", create_evaluate_node(llm))
    builder.add_node("loop_controller", create_loop_controller_node(max_iterations))
    builder.add_node("generate_response", create_generate_response_node(llm, tools_list))

    builder.add_edge("__start__", "planner")
    builder.add_edge("planner", "tool_selection")
    builder.add_edge("tool_selection", "execute")
    builder.add_edge("execute", "evaluate")
    builder.add_edge("evaluate", "loop_controller")
    builder.add_conditional_edges("loop_controller", _route_after_loop)
    builder.add_edge("generate_response", "__end__")

    return builder.compile(checkpointer=checkpointer)


def _route_after_loop(state: AgentState) -> str:
    """Route to generate_response (done) or planner (next iteration)."""
    evaluation = state.get("evaluation", "")
    iteration = state.get("iteration", 0)
    max_iterations = 10
    if evaluation == "done" or iteration >= max_iterations:
        return "generate_response"
    return "planner"
