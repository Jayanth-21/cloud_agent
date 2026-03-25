# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic aggregation and chart-type selection."""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from viz_pipeline.schemas import VizData

from viz_pipeline.schemas import CategoryPoint, TimePoint, VizData


def _parse_date(s: str) -> datetime | None:
    s = (s or "").strip()[:10]
    if len(s) < 10:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None


def aggregate_weekly(time_series: list[TimePoint]) -> list[TimePoint]:
    """Bucket by ISO week start (Monday)."""
    weekly: dict[str, float] = defaultdict(float)
    for p in time_series:
        dt = _parse_date(p.x)
        if dt is None:
            continue
        monday = dt - timedelta(days=dt.weekday())
        key = monday.strftime("%Y-%m-%d")
        weekly[key] += float(p.y)
    return [TimePoint(x=k, y=round(v, 6)) for k, v in sorted(weekly.items())]


def aggregate_monthly(time_series: list[TimePoint]) -> list[TimePoint]:
    monthly: dict[str, float] = defaultdict(float)
    for p in time_series:
        dt = _parse_date(p.x)
        if dt is None:
            continue
        key = dt.strftime("%Y-%m")
        monthly[key] += float(p.y)
    return [TimePoint(x=k, y=round(v, 6)) for k, v in sorted(monthly.items())]


def choose_granularity(num_points: int, user_query: str) -> str:
    q = (user_query or "").lower()
    if "daily" in q or "day by day" in q or "day-to-day" in q or "each day" in q:
        return "daily"
    if "weekly" in q or "by week" in q:
        return "weekly"
    if "monthly" in q or "by month" in q:
        return "monthly"
    if num_points <= 14:
        return "daily"
    if num_points <= 90:
        return "weekly"
    return "monthly"


def choose_chart_type(viz: "VizData", user_query: str) -> str:
    q = (user_query or "").lower()
    if viz.type == "categorical" and viz.categories:
        return "bar"
    if "service" in q or "breakdown" in q or "by region" in q or "categor" in q:
        if viz.categories:
            return "bar"
    if viz.type == "time_series" and viz.time_series:
        return "line"
    if viz.categories:
        return "bar"
    return "line"


def transform_viz_data(viz: VizData, user_query: str) -> VizData:
    out = viz.model_copy(deep=True)
    if out.type == "time_series" and out.time_series:
        n = len(out.time_series)
        g = choose_granularity(n, user_query)
        out.granularity = g  # type: ignore
        if g == "weekly" and n > 1:
            out.time_series = aggregate_weekly(out.time_series)
        elif g == "monthly" and n > 1:
            out.time_series = aggregate_monthly(out.time_series)
    out.chart_type = choose_chart_type(out, user_query)  # type: ignore
    if not out.x_label:
        out.x_label = "Date" if out.type == "time_series" else "Category"
    if not out.y_label:
        out.y_label = "Cost (USD)" if out.total_cost is not None else "Value"
    return out
