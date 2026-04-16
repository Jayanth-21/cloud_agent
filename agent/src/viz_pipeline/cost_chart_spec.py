# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Deterministic Cost Explorer → JSON chart specs for web UI (Plotly).
No LLM: parse get_cost_and_usage / get_cost_forecast outputs only.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from tools.tool_output_unwrap import unwrap_tool_output
from viz_pipeline.pipeline import should_suppress_charts_for_service_query


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _tick_strategy(num_points: int) -> Tuple[str, int]:
    """<=10 days: show all ticks; else sparse 5–10."""
    if num_points <= 10:
        return "all", num_points
    return "sparse", min(10, max(5, min(10, num_points // 3 + 5)))


def _metric_amount(total_block: Any) -> Optional[float]:
    if not isinstance(total_block, dict):
        return None
    for _k, v in total_block.items():
        if isinstance(v, dict) and "Amount" in v:
            return _safe_float(v.get("Amount"))
    return None


def _group_amount(group: Dict[str, Any]) -> Optional[float]:
    for mv in (group.get("Metrics") or {}).values():
        if isinstance(mv, dict) and "Amount" in mv:
            return _safe_float(mv.get("Amount"))
    return None


def _parse_ce_dict(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    charts: List[Dict[str, Any]] = []
    if not isinstance(d, dict) or d.get("error"):
        return charts

    pst = d.get("_period_service_totals") or {}
    by_service = pst.get("by_service") if isinstance(pst, dict) else []
    rbt = d.get("ResultsByTime")
    if not isinstance(rbt, list):
        rbt = []

    # --- Bar: service totals for full period (from authoritative rollup) ---
    if isinstance(by_service, list) and by_service:
        bar_cats: List[Dict[str, Any]] = []
        for row in by_service[:20]:
            if not isinstance(row, dict):
                continue
            lab = str(row.get("service") or "?")[:80]
            amt = _safe_float(row.get("amount_usd"))
            if amt is None or amt <= 0:
                continue
            bar_cats.append({"label": lab, "value": round(amt, 4)})
        if bar_cats:
            period_total = pst.get("period_total_usd")
            try:
                pt = float(period_total) if period_total is not None else None
            except (TypeError, ValueError):
                pt = None
            charts.append(
                {
                    "kind": "bar",
                    "title": "Cost by service (full period)",
                    "xLabel": "Service",
                    "yLabel": "Cost (USD)",
                    "categories": bar_cats,
                    "subtitle": f"Total: ${pt:,.2f} USD" if pt is not None else None,
                }
            )

    # --- Line: grouped by SERVICE (one or more series) ---
    has_groups = False
    for row in rbt:
        if isinstance(row, dict) and (row.get("Groups") or []):
            has_groups = True
            break

    if has_groups:
        # service -> list of {x, y}
        series_map: Dict[str, List[Dict[str, Any]]] = {}
        for row in rbt:
            if not isinstance(row, dict):
                continue
            tp = row.get("TimePeriod") or {}
            start = str(tp.get("Start") or "")[:10]
            if not start:
                continue
            for g in row.get("Groups") or []:
                if not isinstance(g, dict):
                    continue
                keys = g.get("Keys") or []
                name = str(keys[0]) if keys else "Unknown"
                y = _group_amount(g)
                if y is None:
                    continue
                series_map.setdefault(name, []).append({"x": start, "y": round(y, 6)})

        for name in series_map:
            series_map[name].sort(key=lambda p: p["x"])

        if len(series_map) == 1:
            only = next(iter(series_map.items()))
            series_list = [{"name": only[0], "points": only[1]}]
            title = f"Cost over time — {only[0]}"
        elif len(series_map) > 1:
            # Top 8 services by total cost in this grouped result
            totals = {
                k: sum(p["y"] for p in pts) for k, pts in series_map.items()
            }
            ranked = sorted(totals.keys(), key=lambda k: -totals[k])[:8]
            series_list = [{"name": k, "points": series_map[k]} for k in ranked]
            title = "Cost over time by service"
        else:
            series_list = []

        if series_list:
            n = max(len(s["points"]) for s in series_list)
            mode, max_ticks = _tick_strategy(n)
            charts.insert(
                0,
                {
                    "kind": "line",
                    "title": title,
                    "xLabel": "Date",
                    "yLabel": "Cost (USD)",
                    "xTickMode": mode,
                    "maxXTicks": max_ticks,
                    "series": series_list,
                },
            )
        return charts

    # --- Line: single total per period (no group_by) ---
    points: List[Dict[str, Any]] = []
    for row in rbt:
        if not isinstance(row, dict):
            continue
        tp = row.get("TimePeriod") or {}
        start = str(tp.get("Start") or "")[:10]
        if not start:
            continue
        y = _metric_amount(row.get("Total"))
        if y is None:
            groups = row.get("Groups") or []
            s = 0.0
            for g in groups:
                if not isinstance(g, dict):
                    continue
                ga = _group_amount(g)
                if ga is not None:
                    s += ga
            y = s if groups else None
        if y is not None:
            points.append({"x": start, "y": round(y, 6)})

    if points:
        points.sort(key=lambda p: p["x"])
        mode, max_ticks = _tick_strategy(len(points))
        charts.insert(
            0,
            {
                "kind": "line",
                "title": "AWS cost over time",
                "xLabel": "Date",
                "yLabel": "Cost (USD)",
                "xTickMode": mode,
                "maxXTicks": max_ticks,
                "series": [{"name": "Total", "points": points}],
            },
        )

    return charts


def _parse_forecast_dict(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    charts: List[Dict[str, Any]] = []
    if not isinstance(d, dict) or d.get("error"):
        return charts
    frbt = d.get("ForecastResultsByTime")
    if not isinstance(frbt, list):
        return charts
    points: List[Dict[str, Any]] = []
    for row in frbt:
        if not isinstance(row, dict):
            continue
        tp = row.get("TimePeriod") or {}
        start = str(tp.get("Start") or "")[:10]
        mv = _safe_float(row.get("MeanValue"))
        if mv is None and isinstance(row.get("MeanValue"), dict):
            mv = _safe_float(row["MeanValue"].get("Amount"))
        if start and mv is not None:
            points.append({"x": start, "y": round(mv, 6)})
    if not points:
        return charts
    points.sort(key=lambda p: p["x"])
    mode, max_ticks = _tick_strategy(len(points))
    charts.append(
        {
            "kind": "line",
            "title": "Cost forecast",
            "xLabel": "Period",
            "yLabel": "Cost (USD)",
            "xTickMode": mode,
            "maxXTicks": max_ticks,
            "series": [{"name": "Forecast", "points": points}],
        }
    )
    return charts


def _tool_suffix(name: str) -> str:
    n = (name or "").strip()
    return n.split("___")[-1] if "___" in n else n


def build_cost_chart_specs_from_results(
    results: List[dict], user_query: str
) -> List[Dict[str, Any]]:
    """
    Build 0–N chart dicts from the latest cost tool result in results.
    Respects inventory-only suppression (no charts for pure list-Lambda queries).
    """
    if should_suppress_charts_for_service_query(user_query):
        return []
    for r in reversed(results or []):
        if not isinstance(r, dict) or r.get("error"):
            continue
        suffix = _tool_suffix((r.get("name") or "").strip())
        if suffix not in ("get_cost_and_usage", "get_cost_forecast"):
            continue
        raw = r.get("output")
        if raw is None or not str(raw).strip():
            continue
        s = unwrap_tool_output(raw).strip()
        try:
            d = json.loads(s)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(d, dict) or d.get("error"):
            continue
        if suffix == "get_cost_and_usage":
            return _parse_ce_dict(d)
        return _parse_forecast_dict(d)
    return []


def build_cost_chart_specs_envelope(
    results: List[dict], user_query: str
) -> Dict[str, Any]:
    specs = build_cost_chart_specs_from_results(results, user_query)
    if not specs:
        return {}
    return {"version": "cloud_intel_charts/v1", "charts": specs}
