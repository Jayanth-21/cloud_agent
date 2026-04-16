# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Skill context for the graph: no extra LLM call.

All skills are loaded from SKILL.md; a short discovery block (id, name, description) plus full
playbooks are injected via `skill_context` into planner / tool_selection / evaluate /
generate_response. The graph’s own LLM steps choose tools. MCP tools are not filtered by skill
allowlist (all gateway tools are available); YAML `tools` lists remain documentation hints.
"""

import logging
from dataclasses import dataclass

from skills.load import Skill, load_all_skills

logger = logging.getLogger(__name__)


@dataclass
class SkillRouteResult:
    selected_ids: list[str]
    """All skill ids whose playbooks are in context (for logs / UI)."""

    context_markdown: str
    """Injected into planner / tool / evaluate / final prompts."""

    allowed_tool_bases: frozenset[str] | None
    """None = do not filter MCP tools (full gateway surface)."""

    used_full_tool_fallback: bool
    """True: full MCP tool surface (no per-skill allowlist)."""


def _prompt_injection_block(skills: list[Skill]) -> str:
    lines = [
        "## Agent skills",
        "Use the playbooks below when planning and selecting tools. Pick cost tools for billing/spend questions and log/Insights tools for observability questions; both may apply.",
        "",
        "### Quick reference (name + description)",
    ]
    for s in skills:
        lines.append(f"- **`{s.id}`** — *{s.name}*: {s.description.strip()}")
    lines.extend(["", "---", ""])
    lines.append("\n\n".join(s.prompt_block() for s in skills))
    return "\n".join(lines).strip()


def route_skills_for_prompt(_user_prompt: str = "") -> SkillRouteResult:
    """
    Build skill context for the LangGraph prompts. Does not call the LLM.

    `_user_prompt` is unused; kept for a stable call site in runtime_invoke.
    """
    skills = load_all_skills()
    if not skills:
        logger.warning("No SKILL.md packages found under skills/; using full tool surface")
        return SkillRouteResult(
            selected_ids=[],
            context_markdown="",
            allowed_tool_bases=None,
            used_full_tool_fallback=True,
        )

    context = _prompt_injection_block(skills)
    ids = [s.id for s in skills]
    print(f"[AGENT] skill_context injected ids={ids} (graph prompts, no router LLM)", flush=True)
    return SkillRouteResult(
        selected_ids=ids,
        context_markdown=context,
        allowed_tool_bases=None,
        used_full_tool_fallback=True,
    )
