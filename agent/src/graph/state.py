# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""LangGraph state for Plan → Tool Selection → Execute → Evaluate → Iterate."""

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


def _results_reducer(old: list | None, new: list | None) -> list:
    """Accumulate results within a run; reset when new is empty (new conversation turn)."""
    if new is not None and len(new) == 0:
        return []
    return (old or []) + (new or [])


class AgentState(TypedDict, total=False):
    """Shared state across planner, tool_selection, execute, evaluate, loop_controller."""

    messages: Annotated[list[BaseMessage], add_messages]
    plan: str
    scoped_tools: list  # LangChain tools from Gateway (for tool_selection + execute)
    selected_tools: list[dict]
    results: Annotated[list[dict], _results_reducer]  # Accumulate within run; reset when input passes []
    evaluation: str
    iteration: int
    chart_markdown: str  # set by prepare_viz (table + optional PNG markdown)
