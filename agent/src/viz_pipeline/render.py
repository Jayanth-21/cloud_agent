# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Matplotlib → base64 PNG + markdown table."""

import base64
import io
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from viz_pipeline.schemas import VizData


def _is_currency(ylabel: str) -> bool:
    v = (ylabel or "").lower()
    return any(x in v for x in ("usd", "cost", "$", "dollar"))


def _fmt_money(v: float) -> str:
    return f"${v:,.2f}"


def build_table_markdown(viz: VizData) -> str:
    if viz.type == "time_series" and viz.time_series:
        rows = ["| Date | Value |", "| --- | --- |"]
        for p in viz.time_series:
            val = _fmt_money(p.y) if _is_currency(viz.y_label) else f"{p.y:,.4f}"
            rows.append(f"| {p.x} | {val} |")
        return "\n".join(rows)
    if viz.type == "categorical" and viz.categories:
        rows = ["| Category | Value |", "| --- | --- |"]
        for c in viz.categories:
            val = _fmt_money(c.value) if _is_currency(viz.y_label) else f"{c.value:,.4f}"
            rows.append(f"| {c.label[:60]} | {val} |")
        return "\n".join(rows)
    return ""


def render_chart_markdown(viz: VizData) -> str:
    """Return markdown: optional table + embedded PNG."""
    ct = viz.chart_type or "line"
    table = build_table_markdown(viz)
    prefix = (table + "\n\n") if table else ""

    fig, ax = plt.subplots(figsize=(9, 4.2))
    y_label = viz.y_label or "Value"
    x_label = viz.x_label or "X"

    if ct == "line" and viz.time_series:
        xs = [p.x for p in viz.time_series]
        ys = [p.y for p in viz.time_series]
        ax.plot(range(len(xs)), ys, marker="o", linewidth=2)
        ax.set_xticks(range(len(xs)))
        ax.set_xticklabels(xs, rotation=45, ha="right")
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        if _is_currency(y_label):
            ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"${x:,.2f}"))
    elif ct == "bar" and viz.categories:
        labs = [c.label[:30] for c in viz.categories]
        vals = [c.value for c in viz.categories]
        ax.bar(range(len(labs)), vals, color="steelblue", edgecolor="navy", alpha=0.85)
        ax.set_xticks(range(len(labs)))
        ax.set_xticklabels(labs, rotation=45, ha="right")
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        if _is_currency(y_label):
            ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"${x:,.2f}"))
    else:
        plt.close(fig)
        return prefix.strip() if prefix else ""

    ax.set_title(viz.title or "Chart")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("ascii")
    return prefix + f"![Chart](data:image/png;base64,{b64})"
