# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Load Agent Skills from agent/skills/*/SKILL.md (YAML frontmatter + markdown body)."""

from dataclasses import dataclass
from pathlib import Path

import yaml

# agent/src/skills/load.py -> agent/
_AGENT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SKILLS_DIR = _AGENT_ROOT / "skills"


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    description: str
    body: str
    tools: frozenset[str]
    routing_body_chars: int

    def routing_text(self) -> str:
        n = max(0, self.routing_body_chars)
        snippet = (self.body or "")[:n] if n else ""
        return f"{self.description.strip()}\n{snippet}".strip()

    def prompt_block(self) -> str:
        return f"### {self.name} (`{self.id}`)\n{self.body.strip()}"


def skills_root() -> Path:
    override = __import__("os").environ.get("SKILLS_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_SKILLS_DIR.resolve()


def load_all_skills() -> list[Skill]:
    root = skills_root()
    if not root.is_dir():
        return []

    out: list[Skill] = []
    for skill_dir in sorted(root.iterdir()):
        if not skill_dir.is_dir():
            continue
        md = skill_dir / "SKILL.md"
        if not md.is_file():
            continue
        skill = _parse_skill_md(md)
        if skill:
            out.append(skill)
    return out


def _parse_skill_md(path: Path) -> Skill | None:
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return None
    if not raw.lstrip().startswith("---"):
        return None
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return None
    body = parts[2].strip()
    sid = str(meta.get("id") or path.parent.name).strip()
    name = str(meta.get("name") or sid).strip()
    desc = str(meta.get("description") or "").strip()
    tools_raw = meta.get("tools") or []
    if not isinstance(tools_raw, list):
        tools_raw = []
    tools = frozenset(str(t).strip() for t in tools_raw if str(t).strip())
    routing_n = int(meta.get("routing_body_chars") or 900)
    return Skill(
        id=sid,
        name=name,
        description=desc,
        body=body,
        tools=tools,
        routing_body_chars=routing_n,
    )
