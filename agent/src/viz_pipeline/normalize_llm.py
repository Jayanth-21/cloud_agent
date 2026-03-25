# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""LLM fallback: unknown tool JSON → VizData (structured output)."""

import json
import logging
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from viz_pipeline.schemas import VizData

logger = logging.getLogger(__name__)

_SYSTEM = """You are a data normalization engine. Convert the tool JSON into visualization schema only.

Rules:
- DO NOT invent numbers. Copy values exactly from the input.
- DO NOT compute totals unless they appear explicitly in the JSON.
- time_series: use for dates/periods with one numeric value per point (x = date string YYYY-MM-DD when possible).
- categorical: use for breakdowns by name (service, region, etc.).
- If no plottable data, return empty time_series and categories and type \"table\".
- title: short descriptive title."""

_MAX_CHARS = 45000


def normalize_llm(llm: Any, raw: str, tool_suffix: str) -> Optional[VizData]:
    if not raw or not str(raw).strip():
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and parsed.get("error"):
            return None
    except (json.JSONDecodeError, TypeError):
        pass
    snippet = str(raw).strip()[:_MAX_CHARS]
    try:
        structured = llm.with_structured_output(VizData).invoke(
            [
                SystemMessage(content=_SYSTEM),
                HumanMessage(
                    content=f"Tool name suffix: {tool_suffix}\n\nJSON:\n{snippet}"
                ),
            ]
        )
    except Exception as e:
        logger.warning("normalize_llm failed: %s", e)
        return None
    if not isinstance(structured, VizData):
        return None
    has_ts = bool(structured.time_series)
    has_cat = bool(structured.categories)
    if not has_ts and not has_cat:
        return None
    if has_cat and not has_ts:
        structured = structured.model_copy(update={"type": "categorical"})
    elif has_ts and not has_cat:
        structured = structured.model_copy(update={"type": "time_series"})
    return structured
