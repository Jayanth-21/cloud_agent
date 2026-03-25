# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Visualization tool: render chart (matplotlib) + optional markdown table from structured data.
Returns a single string: table (if include_table) + markdown image ![Chart](data:image/png;base64,...)
so the agent can include it in the response and the UI can render both.
"""

import base64
import io
import json
from typing import Any, Literal, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from tools.tool_output_unwrap import unwrap_tool_output
from viz_pipeline.pipeline import should_suppress_charts_for_service_query
from tools.viz_normalizer import format_normalization_footer, normalize_visualization_input


def _parse_data(data_input: str | list) -> list[dict]:
    """Parse data from JSON string or list. Return list of dicts with normalized keys."""
    if isinstance(data_input, list):
        items = data_input
    else:
        try:
            raw = json.loads(data_input)
            if isinstance(raw, list):
                items = raw
            elif isinstance(raw, dict) and "Results" in raw:
                items = raw.get("Results", [])
            elif isinstance(raw, dict) and "results" in raw:
                items = raw.get("results", [])
            else:
                items = [raw] if raw else []
        except (json.JSONDecodeError, TypeError):
            return []
    return [i if isinstance(i, dict) else {} for i in items]


def _normalize_series(items: list[dict]) -> tuple[list[str], list[float]]:
    """
    Extract labels and values for bar/pie, or x/y for line.
    Tries common keys: label, name, date, period, value, amount, cost, total.
    """
    labels = []
    values = []
    for row in items:
        if not row:
            continue
        # Label: prefer label, name, date, period, Group (Cost Explorer), TimePeriod
        label = (
            row.get("label")
            or row.get("name")
            or row.get("date")
            or row.get("period")
            or row.get("TimePeriod", {}).get("Start")
            or row.get("Group")
            or (list(row.values())[0] if row else "")
        )
        if isinstance(label, dict):
            label = str(label.get("Start", label))
        # Value: prefer value, amount, cost, total, UnblendedCost, etc.
        val = (
            row.get("value")
            or row.get("amount")
            or row.get("cost")
            or row.get("total")
            or row.get("UnblendedCost")
            or row.get("Metrics", {}).get("UnblendedCost", {}).get("Amount")
        )
        if val is None:
            for k, v in row.items():
                if isinstance(v, (int, float)) and k != "count":
                    val = v
                    break
        try:
            num = float(val) if val is not None else 0.0
        except (TypeError, ValueError):
            num = 0.0
        labels.append(str(label)[:80])
        values.append(num)
    return labels, values


def _is_currency(value_label: str) -> bool:
    """True if value_label indicates money (USD, cost, etc.)."""
    if not value_label:
        return False
    v = value_label.lower()
    return any(x in v for x in ("usd", "cost", "$", "dollar", "amount"))


def _format_value(val: float, value_label: str) -> str:
    """Format a number for table display; use $ for currency."""
    if _is_currency(value_label):
        return f"${val:,.2f}"
    return f"{val:,.2f}"


def _build_markdown_table(
    labels: list[str],
    values: list[float],
    category_label: str = "Category",
    value_label: str = "Amount",
) -> str:
    """Build a markdown table from labels and values with dynamic column headers."""
    if not labels:
        return ""
    col_value = value_label or "Amount"
    col_cat = category_label or "Category"
    rows = [f"| {col_cat} | {col_value} |", "| --- | --- |"]
    for lab, val in zip(labels, values):
        rows.append(f"| {lab} | {_format_value(val, value_label)} |")
    return "\n".join(rows)


def _render_chart(
    chart_type: Literal["line", "bar", "pie"],
    labels: list[str],
    values: list[float],
    title: str,
    value_label: str = "Value",
    category_label: str = "Category",
) -> str:
    """Render chart with matplotlib; return base64 PNG. Uses value_label/category_label for axes and currency formatting."""
    if not labels or not values:
        return ""
    fig, ax = plt.subplots(figsize=(8, 4))
    y_label = value_label or "Value"
    x_label = category_label or "Category"
    if chart_type == "line":
        ax.plot(range(len(labels)), values, marker="o", linewidth=2)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_ylabel(y_label)
        ax.set_xlabel(x_label)
    elif chart_type == "bar":
        x = range(len(labels))
        ax.bar(x, values, color="steelblue", edgecolor="navy", alpha=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_ylabel(y_label)
        ax.set_xlabel(x_label)
    elif chart_type == "pie":
        ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90)
        if value_label:
            fig.text(0.5, 0.02, f"Values: {value_label}", ha="center", fontsize=9)
    ax.set_title(title or "Chart")
    if _is_currency(value_label) and chart_type in ("line", "bar"):
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"${x:,.2f}"))
    if chart_type == "pie":
        fig.subplots_adjust(bottom=0.08)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


class VisualizeDataInput(BaseModel):
    """Input for the visualization tool."""

    data: str = Field(
        description=(
            "JSON string: either (1) raw output from get_cost_and_usage, get_cost_forecast, "
            "get_metric_data/analyze_metric, analyze_log_group, or complete get_logs_insight_query_results "
            "(auto-normalized), or (2) a list of {label/date, value/amount}."
        )
    )
    chart_type: Literal["line", "bar", "pie"] = Field(
        description="Use 'line' for time-series (forecast, daily costs); 'bar' or 'pie' for categorical breakdown (by service, by region)."
    )
    title: str = Field(description="Chart and table title.")
    include_table: bool = Field(
        default=True,
        description="When True, include a markdown table in the output so the user gets both table and chart (recommended for costs).",
    )
    value_label: str = Field(
        default="Amount",
        description="Label and unit for the numeric values, e.g. 'Cost (USD)', 'Number of requests', 'Count'. Use 'Cost (USD)' for cost/forecast data so axes and table show $.",
    )
    category_label: str = Field(
        default="Category",
        description="Label for the categories (x-axis or table first column), e.g. 'Region', 'Service', 'Date', 'Day'.",
    )


def _visualize_impl(
    data: str,
    chart_type: Literal["line", "bar", "pie"],
    title: str,
    include_table: bool,
    value_label: str = "Amount",
    category_label: str = "Category",
) -> str:
    normalized, norm_note = normalize_visualization_input(data)
    if normalized:
        items = normalized
    else:
        items = _parse_data(data)
    labels, values = _normalize_series(items)
    if not labels or not values:
        hint = (
            " Raw tool output (e.g. get_cost_and_usage, get_cost_forecast, get_metric_data, "
            "or Logs Insights results when complete) is auto-normalized when possible."
        )
        return (
            "No valid data to visualize. Ensure 'data' is JSON: either a list of "
            "{label/date, value/amount} or raw API output from cost, forecast, metrics, or logs." + hint
        )
    table_md = ""
    if include_table:
        table_md = _build_markdown_table(labels, values, category_label, value_label) + "\n\n"
    b64 = _render_chart(chart_type, labels, values, title or "Chart", value_label, category_label)
    if not b64:
        return table_md.strip() or "Could not generate chart."
    out = table_md + "![Chart](data:image/png;base64," + b64 + ")"
    if normalized:
        out += format_normalization_footer(norm_note, len(labels))
    return out


# Tool name suffix (after ___) -> (chart_type, title, value_label, category_label)
_AUTO_VIZ_BY_SUFFIX: dict[str, tuple[str, str, str, str]] = {
    "get_cost_and_usage": ("line", "AWS cost over time", "Cost (USD)", "Date"),
    "get_cost_forecast": ("line", "Cost forecast", "Cost (USD)", "Date"),
    "get_metric_data": ("line", "Metric over time", "Value", "Time"),
    "analyze_metric": ("line", "Metric over time", "Value", "Time"),
    "analyze_log_group": ("line", "Log volume over time", "Count", "Time"),
    "get_logs_insight_query_results": ("line", "Logs Insights", "Value", "Row"),
}


def build_auto_viz_from_results(
    results: list, user_query: Optional[str] = None
) -> Optional[str]:
    """
    Build chart/table markdown from the latest eligible tool JSON in results (server-side).
    Avoids the LLM embedding large JSON in visualize_data (max_tokens / parse failures).
    """
    if user_query and should_suppress_charts_for_service_query(user_query):
        return None
    if not results:
        return None
    for r in reversed(results):
        if not isinstance(r, dict) or r.get("error"):
            continue
        name = (r.get("name") or "").strip()
        suffix = name.split("___")[-1] if "___" in name else name
        cfg = _AUTO_VIZ_BY_SUFFIX.get(suffix)
        if not cfg:
            continue
        raw = r.get("output")
        if raw is None or not str(raw).strip():
            continue
        s = unwrap_tool_output(raw).strip()
        try:
            parsed = json.loads(s)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict) and parsed.get("error"):
            continue
        chart_type, title, value_label, category_label = cfg
        ct = chart_type  # type: Literal["line", "bar", "pie"]
        viz = _visualize_impl(s, ct, title, True, value_label, category_label)
        if viz and not viz.startswith("No valid data"):
            return viz
    return None


def _tool_message_body_text(msg: Any) -> str:
    """String body from a LangChain ToolMessage (or serialized dict)."""
    if msg is None:
        return ""
    if isinstance(msg, dict):
        c = msg.get("content")
    else:
        c = getattr(msg, "content", None)
        name = (getattr(msg, "name", None) or "").strip()
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts: list[str] = []
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(str(b.get("text", "")))
            elif isinstance(b, str):
                parts.append(b)
        return "".join(parts)
    return str(c) if c is not None else ""


def _is_tool_message(msg: Any) -> bool:
    if isinstance(msg, dict):
        t = msg.get("type") or msg.get("type_")
        if t == "tool":
            return True
        if (msg.get("role") or "").lower() == "tool":
            return True
    return getattr(msg, "type", None) == "tool"


def _tool_message_name(msg: Any) -> str:
    if isinstance(msg, dict):
        return (msg.get("name") or msg.get("tool_name") or "").strip()
    return (getattr(msg, "name", None) or "").strip()


def build_auto_viz_from_conversation(
    messages: list, user_query: Optional[str] = None
) -> Optional[str]:
    """
    When state.results is empty (e.g. cleared by checkpoint merge) but ToolMessages
    still carry get_cost_and_usage JSON, build the same chart from the latest match.
    """
    if user_query and should_suppress_charts_for_service_query(user_query):
        return None
    if not messages:
        return None
    for m in reversed(messages):
        if not _is_tool_message(m):
            continue
        body = unwrap_tool_output(_tool_message_body_text(m)).strip()
        if not body:
            continue
        name = _tool_message_name(m)
        suffix = name.split("___")[-1] if "___" in name else name
        if suffix in _AUTO_VIZ_BY_SUFFIX:
            return build_auto_viz_from_results(
                [{"name": name or f"___{suffix}", "output": body}],
                user_query=user_query,
            )
        if '"ResultsByTime"' in body or '"ResultsByTime":' in body:
            return build_auto_viz_from_results(
                [{"name": "___get_cost_and_usage", "output": body}],
                user_query=user_query,
            )
        if "ForecastResultsByTime" in body:
            return build_auto_viz_from_results(
                [{"name": "___get_cost_forecast", "output": body}],
                user_query=user_query,
            )
    return None


class VisualizeDataTool(BaseTool):
    """Tool to visualize data as a chart (PNG base64) and optional markdown table."""

    name: str = "visualize_data"
    description: str = (
        "Render cost, forecast, metrics, logs, or other numeric data as a chart and optional markdown table. "
        "You may pass the raw JSON string returned by get_cost_and_usage, get_cost_forecast, get_metric_data, "
        "analyze_log_group (log events), or get_logs_insight_query_results (when status is Complete); "
        "the tool will normalize daily totals, forecast points, metric timestamps, log counts by day, or Insights tables. "
        "Use chart_type='line' for time-series; 'bar' or 'pie' for breakdowns. "
        "Set value_label (e.g. Cost (USD), Count) and category_label (Date, Service, etc.)."
    )
    args_schema: type[BaseModel] = VisualizeDataInput

    def _run(
        self,
        data: str,
        chart_type: Literal["line", "bar", "pie"],
        title: str,
        include_table: bool = True,
        value_label: str = "Amount",
        category_label: str = "Category",
        **kwargs: Any,
    ) -> str:
        return _visualize_impl(data, chart_type, title, include_table, value_label, category_label)

    async def _arun(
        self,
        data: str,
        chart_type: Literal["line", "bar", "pie"],
        title: str,
        include_table: bool = True,
        value_label: str = "Amount",
        category_label: str = "Category",
        **kwargs: Any,
    ) -> str:
        return _visualize_impl(data, chart_type, title, include_table, value_label, category_label)


def get_visualization_tool() -> BaseTool:
    """Return the visualization tool instance."""
    return VisualizeDataTool()
