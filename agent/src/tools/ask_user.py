# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Ask-the-user tool: when the agent needs more information (e.g. date range, service name),
it calls this tool with the question. The tool returns that question so the agent's
response is the clarification; the client can show it and the user's next message
continues the conversation (session memory provides context).
"""

from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


class AskUserInput(BaseModel):
    """Input for the ask_user tool."""

    question: str = Field(
        description="The exact question to show the user (e.g. 'Please specify the date range (e.g. last 7 days, or start and end dates).' or 'Which service or region do you want cost for?')."
    )


def _ask_user_impl(question: str) -> str:
    """Return the question so it becomes the agent's reply to the user."""
    return (question or "").strip() or "Please provide the missing details."


class AskUserTool(BaseTool):
    """Tool to ask the user for more information when the request is vague or missing required details."""

    name: str = "ask_user"
    description: str = (
        "Ask the user for more information when the request is vague or missing required details. "
        "Use for: date range (e.g. for cost or forecast queries), service name, region, or other specifics. "
        "Pass the exact question to show the user. Call this when the user did not specify time range or other required parameters (e.g. 'what are my costs?' without a date range). "
        "Do not call get_cost_and_usage or get_cost_forecast until you have the needed details, or use ask_user first to get them."
    )
    args_schema: type[BaseModel] = AskUserInput

    def _run(self, question: str, **kwargs: Any) -> str:
        return _ask_user_impl(question)

    async def _arun(self, question: str, **kwargs: Any) -> str:
        return _ask_user_impl(question)


def get_ask_user_tool() -> BaseTool:
    """Return the ask_user tool instance."""
    return AskUserTool()
