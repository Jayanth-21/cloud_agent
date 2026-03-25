# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Normalize MCP/Bedrock tool results to a plain string (e.g. Cost Explorer JSON).

Gateway may return content as a list of blocks: [{'type': 'text', 'text': '{...json...}'}].
Viz and json.loads need the inner JSON string.
"""

from __future__ import annotations

import ast
import json
from typing import Any, Optional


def _extract_from_block_list(items: list) -> Optional[str]:
    """First text block whose body looks like JSON object/array."""
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        t = item.get("text")
        if not isinstance(t, str):
            continue
        u = t.strip()
        if u.startswith("{") or u.startswith("["):
            return u
    return None


def unwrap_tool_output(content: Any) -> str:
    """
    If content is Bedrock-style content blocks, return inner JSON (or first JSON text).
    Otherwise return str(content) unchanged.
    """
    if content is None:
        return ""

    if isinstance(content, dict):
        try:
            return json.dumps(content)
        except (TypeError, ValueError):
            return str(content)

    if isinstance(content, list):
        inner = _extract_from_block_list(content)
        if inner:
            return inner
        texts = [
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        if texts:
            return "\n".join(texts) if len(texts) > 1 else texts[0]
        try:
            return json.dumps(content)
        except (TypeError, ValueError):
            return str(content)

    s = str(content).strip()
    if not s:
        return ""

    # Already plain JSON object string
    if s.startswith("{") and s.endswith("}"):
        try:
            json.loads(s)
            return s
        except json.JSONDecodeError:
            pass

    # JSON array of blocks (double-quoted)
    if s.startswith("["):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                inner = _extract_from_block_list(parsed)
                if inner:
                    return inner
        except json.JSONDecodeError:
            pass
        # Python repr: [{'type': 'text', 'text': '{"a":1}'}]
        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, list):
                inner = _extract_from_block_list(parsed)
                if inner:
                    return inner
        except (ValueError, SyntaxError):
            pass

    return s
