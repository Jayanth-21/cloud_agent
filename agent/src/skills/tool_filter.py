# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Filter MCP tools by skill-declared allowlists (+ meta tools)."""

from typing import Any

# Always available so the agent can list capabilities and clarify.
META_TOOL_NAMES = frozenset({"list_available_tools"})


def tool_name_base(name: str) -> str:
    if "___" in name:
        return name.split("___")[-1]
    return name


def filter_tools_by_allowlist(
    tools: list[Any],
    allowed_bases: frozenset[str] | None,
) -> list[Any]:
    """
    If allowed_bases is None, return all tools (no skill restriction).
    Otherwise keep tools whose base name is in allowed_bases | META_TOOL_NAMES.
    """
    if allowed_bases is None:
        return list(tools)
    allow = allowed_bases | META_TOOL_NAMES
    return [t for t in tools if tool_name_base(getattr(t, "name", "")) in allow]
