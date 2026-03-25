"""Deterministic normalization for known AWS / MCP tool shapes."""

import json
from typing import Any, Optional

from viz_pipeline.schemas import CategoryPoint, TimePoint, VizData


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def normalize_get_cost_and_usage(raw: str, user_query: str) -> Optional[VizData]:
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(d, dict) or d.get("error"):
        return None
    q = (user_query or "").lower()

    wants_service = any(
        k in q
        for k in (
            "service",
            "by service",
            "breakdown",
            "driver",
            "top service",
            "which service",
        )
    )
    pst = d.get("_period_service_totals") or {}
    by_svc = pst.get("by_service") or []
    if wants_service and isinstance(by_svc, list) and by_svc:
        cats: list[CategoryPoint] = []
        for row in by_svc[:25]:
            if not isinstance(row, dict):
                continue
            amt = _safe_float(row.get("amount_usd"))
            if amt is None or amt <= 0:
                continue
            lab = str(row.get("service") or "?")[:50]
            cats.append(CategoryPoint(label=lab, value=round(amt, 6)))
        if cats:
            cats.sort(key=lambda c: -c.value)
            total = pst.get("period_total_usd")
            if total is not None:
                try:
                    total = float(total)
                except (TypeError, ValueError):
                    total = None
            return VizData(
                type="categorical",
                categories=cats,
                title="Cost by service",
                x_label="Service",
                y_label="Cost (USD)",
                total_cost=total,
            )

    rbt = d.get("ResultsByTime")
    if not isinstance(rbt, list):
        return None
    points: list[TimePoint] = []
    for row in rbt:
        if not isinstance(row, dict):
            continue
        tp = row.get("TimePeriod") or {}
        start = str(tp.get("Start") or "")[:16]
        if not start:
            continue
        total = row.get("Total") or {}
        y: Optional[float] = None
        if isinstance(total, dict):
            for _k, v in total.items():
                if isinstance(v, dict) and "Amount" in v:
                    y = _safe_float(v.get("Amount"))
                    break
        if y is None:
            groups = row.get("Groups") or []
            s = 0.0
            for g in groups:
                if not isinstance(g, dict):
                    continue
                for mv in (g.get("Metrics") or {}).values():
                    if isinstance(mv, dict) and "Amount" in mv:
                        t = _safe_float(mv.get("Amount"))
                        if t is not None:
                            s += t
                        break
            y = s if groups else None
        if y is not None:
            points.append(TimePoint(x=start[:10], y=round(y, 6)))
    if not points:
        return None
    total = pst.get("period_total_usd") if isinstance(pst, dict) else None
    if total is not None:
        try:
            total = float(total)
        except (TypeError, ValueError):
            total = None
    return VizData(
        type="time_series",
        time_series=sorted(points, key=lambda p: p.x),
        title="AWS cost over time",
        x_label="Date",
        y_label="Cost (USD)",
        total_cost=total,
    )


def normalize_get_cost_forecast(raw: str, _user_query: str) -> Optional[VizData]:
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(d, dict) or d.get("error"):
        return None
    frbt = d.get("ForecastResultsByTime")
    if not isinstance(frbt, list):
        return None
    points: list[TimePoint] = []
    for row in frbt:
        if not isinstance(row, dict):
            continue
        tp = row.get("TimePeriod") or {}
        start = str(tp.get("Start") or "")[:10]
        mv = _safe_float(row.get("MeanValue"))
        if mv is None and isinstance(row.get("MeanValue"), dict):
            mv = _safe_float(row["MeanValue"].get("Amount"))
        if start and mv is not None:
            points.append(TimePoint(x=start, y=round(mv, 6)))
    if not points:
        return None
    return VizData(
        type="time_series",
        time_series=sorted(points, key=lambda p: p.x),
        title="Cost forecast",
        x_label="Period",
        y_label="Cost (USD)",
    )


def normalize_get_metric_data(raw: str, _user_query: str) -> Optional[VizData]:
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(d, dict) or d.get("error"):
        return None
    mdr = d.get("MetricDataResults")
    if not isinstance(mdr, list) or not mdr:
        return None
    first = mdr[0]
    if not isinstance(first, dict):
        return None
    ts = first.get("Timestamps") or []
    vals = first.get("Values") or []
    if not isinstance(ts, list) or not isinstance(vals, list):
        return None
    n = min(len(ts), len(vals))
    points: list[TimePoint] = []
    for i in range(n):
        v = _safe_float(vals[i])
        if v is None:
            continue
        lab = ts[i]
        if hasattr(lab, "strftime"):
            lab = lab.strftime("%Y-%m-%d %H:%M")
        points.append(TimePoint(x=str(lab)[:32], y=round(v, 6)))
    if len(points) < 1:
        return None
    return VizData(
        type="time_series",
        time_series=points,
        title="Metric over time",
        x_label="Time",
        y_label="Value",
    )


def normalize_analyze_log_group(raw: str, _user_query: str) -> Optional[VizData]:
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(d, dict):
        return None
    events = d.get("events")
    if not isinstance(events, list) or not events:
        return None
    from collections import defaultdict

    counts: dict[str, int] = defaultdict(int)
    for ev in events:
        if not isinstance(ev, dict):
            continue
        t = ev.get("timestamp")
        try:
            ms = int(t)
            from datetime import datetime, timezone

            day = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
            counts[day] += 1
        except (TypeError, ValueError, OSError):
            continue
    if len(counts) < 1:
        return None
    points = [TimePoint(x=k, y=float(v)) for k, v in sorted(counts.items())]
    return VizData(
        type="time_series",
        time_series=points,
        title="Log events per day",
        x_label="Date",
        y_label="Count",
    )


_PYTHON_HANDLERS = {
    "get_cost_and_usage": normalize_get_cost_and_usage,
    "get_cost_forecast": normalize_get_cost_forecast,
    "get_metric_data": normalize_get_metric_data,
    "analyze_log_group": normalize_analyze_log_group,
}


def normalize_python(raw: str, tool_suffix: str, user_query: str) -> Optional[VizData]:
    fn = _PYTHON_HANDLERS.get(tool_suffix)
    if not fn:
        return None
    return fn(raw, user_query)
