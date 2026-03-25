"""Router: Python normalize → optional LLM → transform → conditional render."""

import json
import logging
from typing import Any, List, Optional, Tuple

from tools.tool_output_unwrap import unwrap_tool_output
from viz_pipeline.normalize_llm import normalize_llm
from viz_pipeline.normalize_python import _PYTHON_HANDLERS, normalize_python
from viz_pipeline.render import render_chart_markdown
from viz_pipeline.schemas import VizData
from viz_pipeline.transform import transform_viz_data

logger = logging.getLogger(__name__)

# Prefer Python for these; LLM if Python returns None
_SUFFIXES_TRY_LLM = frozenset(
    {
        "get_cost_and_usage",
        "get_cost_forecast",
        "get_metric_data",
        "analyze_log_group",
        "get_logs_insight_query_results",
        "analyze_metric",
        "execute_log_insights_query",
    }
)


def _tool_suffix(name: str) -> str:
    n = (name or "").strip()
    return n.split("___")[-1] if "___" in n else n


def _latest_tool_payload(
    results: List[dict], messages: List[Any]
) -> Tuple[Optional[str], str]:
    """Return (raw_json, tool_suffix) from latest visualizable tool."""
    for r in reversed(results or []):
        if not isinstance(r, dict) or r.get("error"):
            continue
        name = (r.get("name") or "").strip()
        suffix = _tool_suffix(name)
        if suffix in ("ask_user", "visualize_data", "get_today_date"):
            continue
        raw = r.get("output")
        if raw is None or not str(raw).strip():
            continue
        s = unwrap_tool_output(raw).strip()
        try:
            jd = json.loads(s)
            if isinstance(jd, dict) and jd.get("error"):
                continue
        except (json.JSONDecodeError, TypeError):
            continue
        if suffix not in _VIZ_CHART_SOURCE_SUFFIXES:
            continue
        return s, suffix
    # Messages fallback (checkpoint wiped results)
    for m in reversed(messages or []):
        body = unwrap_tool_output(_msg_tool_body(m))
        if not body or not body.strip().startswith("{"):
            continue
        name = _msg_tool_name(m)
        suffix = _tool_suffix(name)
        if suffix == "ask_user":
            continue
        try:
            jd = json.loads(body)
            if isinstance(jd, dict) and jd.get("error"):
                continue
        except (json.JSONDecodeError, TypeError):
            continue
        if not suffix or suffix == "visualize_data":
            suffix = _infer_suffix_from_json(body)
        if suffix not in _VIZ_CHART_SOURCE_SUFFIXES:
            continue
        return body.strip(), suffix
    return None, ""


def _infer_suffix_from_json(body: str) -> str:
    if "ForecastResultsByTime" in body:
        return "get_cost_forecast"
    if "MetricDataResults" in body:
        return "get_metric_data"
    if "ResultsByTime" in body:
        return "get_cost_and_usage"
    if '"events"' in body and "timestamp" in body:
        return "analyze_log_group"
    return "get_logs_insight_query_results"


def _msg_tool_name(m: Any) -> str:
    if isinstance(m, dict):
        return (m.get("name") or m.get("tool_name") or "").strip()
    return (getattr(m, "name", None) or "").strip()


def _msg_tool_body(m: Any) -> str:
    if isinstance(m, dict):
        if (m.get("type") or m.get("type_")) != "tool" and (m.get("role") or "").lower() != "tool":
            return ""
        c = m.get("content")
    else:
        if getattr(m, "type", None) != "tool":
            return ""
        c = getattr(m, "content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(str(b.get("text", "")))
            elif isinstance(b, str):
                parts.append(b)
        return "".join(parts)
    return str(c or "")


def should_skip_viz(user_query: str) -> bool:
    q = (user_query or "").lower()
    if any(
        x in q
        for x in (
            "no chart",
            "without chart",
            "no graph",
            "no visualization",
            "text only",
            "just tell me",
        )
    ):
        return True
    return False


# Only these tools may drive prepare_viz (ignore stray CE calls on inventory turns).
_VIZ_CHART_SOURCE_SUFFIXES = frozenset(
    {
        "get_cost_and_usage",
        "get_cost_forecast",
        "get_metric_data",
        "analyze_metric",
        "analyze_log_group",
        "get_logs_insight_query_results",
        "execute_log_insights_query",
    }
)


def should_suppress_charts_for_service_query(user_query: str) -> bool:
    """
    Skip charts when the user asked for inventory / resources / listings without
    cost, metrics, or time-series intent (avoids CE charts on 'list my Lambdas').
    """
    q = (user_query or "").lower().strip()
    if not q:
        return False
    if should_skip_viz(q):
        return True

    wants_observability_or_cost = any(
        x in q
        for x in (
            "cost",
            "spend",
            "billing",
            "bill ",
            "forecast",
            "how much",
            "usd",
            " dollar",
            "price",
            "chart",
            "graph",
            "trend",
            "over time",
            "daily ",
            "weekly ",
            "monthly ",
            "usage cost",
            "metric",
            "metrics",
            "cloudwatch",
            "alarm",
            "log insight",
            "insights query",
            "cpu ",
            "memory ",
            "invocation",
            "latency",
            "error rate",
            "throughput",
            "resultsbytime",
            "cost explorer",
            "breakdown",
            "by service",
            # Cost-implied without saying "cost" (avoid suppressing e.g. "most expensive Lambdas")
            "expensive",
            "cheapest",
            "highest",
            "top spend",
            "top spender",
            "top spenders",
            "spender",
            "spenders",
            "waste",
            "wasted",
            "anomaly",
            "spike",
            "optimize",
            "optimization",
            "savings",
            "drivers",
        )
    )
    if wants_observability_or_cost:
        return False

    inventory_or_listing = any(
        x in q
        for x in (
            "lambda function",
            "lambdas",
            " lambda ",
            "functions in",
            "functions do i",
            "functions i have",
            "which lambda",
            "how many lambda",
            "what lambda",
            "list lambda",
            "my lambda",
            "ecs cluster",
            "ecs service",
            "ecs services",
            "log group",
            "log groups",
            "describe ",
            "list my ",
            "what resources",
            "resources in",
            "resources do i",
            "discovered resource",
            "config recorder",
            "trail event",
            "lookup event",
            "list event",
            "multimedia",
            "batch ",
            "unprocessed",
            "s3 bucket",
            "buckets ",
            "dynamodb table",
            "api gateway",
            "step function",
            "state machine",
            "what services",
            "services do i",
            "services i have",
            "inventory",
            "what's deployed",
            "what is deployed",
        )
    )
    return inventory_or_listing


def should_emit_chart(viz: VizData) -> bool:
    if viz.type == "time_series" and len(viz.time_series) >= 1:
        return True
    if viz.type == "categorical" and len(viz.categories) >= 1:
        return True
    return False


def run_visualization_pipeline(
    llm: Any,
    results: List[dict],
    messages: List[Any],
    user_query: str,
) -> str:
    """
    Returns markdown (table + chart) or empty string.
    """
    if should_suppress_charts_for_service_query(user_query):
        return ""
    if any(
        isinstance(r, dict) and (r.get("name") or "").strip() == "ask_user" and "error" not in r
        for r in (results or [])
    ):
        return ""

    raw, suffix = _latest_tool_payload(results, messages)
    if not raw:
        return ""

    viz: Optional[VizData] = None
    if suffix in _PYTHON_HANDLERS:
        viz = normalize_python(raw, suffix, user_query)
    if viz is None and (
        suffix in _SUFFIXES_TRY_LLM or suffix in _PYTHON_HANDLERS
    ):
        viz = normalize_llm(llm, raw, suffix)

    if viz is None:
        return ""
    try:
        viz = transform_viz_data(viz, user_query)
    except Exception as e:
        logger.exception("transform_viz_data: %s", e)
        return ""
    if not should_emit_chart(viz):
        return ""
    try:
        return render_chart_markdown(viz)
    except Exception as e:
        logger.exception("render_chart_markdown: %s", e)
        return ""
