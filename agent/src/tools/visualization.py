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
from typing import Any, Literal

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


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
        description="JSON string: array of objects with label/name/date and value/amount/cost (e.g. from get_cost_and_usage or get_cost_forecast)."
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
    items = _parse_data(data)
    labels, values = _normalize_series(items)
    if not labels or not values:
        return "No valid data to visualize. Ensure 'data' is a JSON array of objects with label/name/date and value/amount/cost."
    table_md = ""
    if include_table:
        table_md = _build_markdown_table(labels, values, category_label, value_label) + "\n\n"
    b64 = _render_chart(chart_type, labels, values, title or "Chart", value_label, category_label)
    if not b64:
        return table_md.strip() or "Could not generate chart."
    return table_md + "![Chart](data:image/png;base64," + b64 + ")"


class VisualizeDataTool(BaseTool):
    """Tool to visualize data as a chart (PNG base64) and optional markdown table."""

    name: str = "visualize_data"
    description: str = (
        "Render cost, forecast, or other numeric data as a chart and optionally a markdown table. "
        "Call this after you have data from get_cost_and_usage, get_cost_forecast, or similar. "
        "Use chart_type='line' for time-series (e.g. daily costs, forecast); use 'bar' or 'pie' for categorical breakdown (e.g. by service). "
        "Set value_label and category_label so axes and table are clear: e.g. value_label='Cost (USD)' and category_label='Region' for cost by region; value_label='Number of requests' for request counts; category_label='Date' or 'Service' or 'Region' as appropriate."
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
