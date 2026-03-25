# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Normalize raw Lambda / AWS API tool outputs into a flat series for visualize_data.

Handles: get_cost_and_usage (ResultsByTime + Total or Groups), get_cost_forecast,
get_metric_data / analyze_metric, analyze_log_group (filter_log_events),
get_logs_insight_query_results. Falls back when shape is unknown or empty.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Avoid huge charts / token blowups
MAX_SERIES_POINTS = 500


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _truncate_series(series: list[dict[str, Any]], max_points: int = MAX_SERIES_POINTS) -> list[dict[str, Any]]:
    if len(series) <= max_points:
        return series
    logger.warning("viz_normalizer: truncating series from %d to %d points", len(series), max_points)
    return series[-max_points:]


def _merge_rbt_duplicate_periods(rbt: list[Any]) -> list[dict[str, Any]]:
    """
    Cost Explorer may return multiple rows per TimePeriod.Start across pages.
    Merge Groups; sum Total metric amounts when both rows have Total.
    """
    order: list[str] = []
    by_start: dict[str, dict[str, Any]] = {}
    for period in rbt:
        if not isinstance(period, dict):
            continue
        tp = period.get("TimePeriod") or {}
        start = str(tp.get("Start") or "")
        if not start:
            continue
        if start not in by_start:
            order.append(start)
            by_start[start] = {
                "TimePeriod": dict(tp) if isinstance(tp, dict) else {"Start": start},
                "Groups": list(period.get("Groups") or []),
                "Total": period.get("Total"),
            }
        else:
            by_start[start]["Groups"].extend(list(period.get("Groups") or []))
            t_new = period.get("Total")
            t_old = by_start[start].get("Total")
            if isinstance(t_new, dict) and isinstance(t_old, dict):
                merged_t: dict[str, Any] = dict(t_old)
                for mk, mv in t_new.items():
                    if isinstance(mv, dict) and "Amount" in mv:
                        try:
                            a = float((merged_t.get(mk) or {}).get("Amount") or 0) + float(
                                mv.get("Amount") or 0
                            )
                        except (TypeError, ValueError):
                            a = float(mv.get("Amount") or 0)
                        unit = mv.get("Unit") or (merged_t.get(mk) or {}).get("Unit") or "USD"
                        merged_t[mk] = {**mv, "Amount": str(a), "Unit": unit}
                    elif mk not in merged_t:
                        merged_t[mk] = mv
                by_start[start]["Total"] = merged_t
            elif isinstance(t_new, dict) and not t_old:
                by_start[start]["Total"] = dict(t_new)
    out: list[dict[str, Any]] = []
    for start in order:
        b = by_start[start]
        row: dict[str, Any] = {"TimePeriod": b["TimePeriod"], "Groups": b["Groups"]}
        if b.get("Total") is not None:
            row["Total"] = b["Total"]
        out.append(row)
    return out


def _normalize_cost_explorer(d: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    """ResultsByTime: sum Total or sum all Groups per period for daily/monthly totals."""
    rbt = d.get("ResultsByTime")
    if not isinstance(rbt, list) or not rbt:
        return None
    rbt = _merge_rbt_duplicate_periods(rbt)
    out: list[dict[str, Any]] = []
    for period in rbt:
        if not isinstance(period, dict):
            continue
        tp = period.get("TimePeriod") or {}
        start = tp.get("Start") or ""
        total_block = period.get("Total") or {}
        value: Optional[float] = None
        if total_block:
            for _k, v in total_block.items():
                if isinstance(v, dict) and "Amount" in v:
                    value = _safe_float(v.get("Amount"))
                    if value is not None:
                        break
        if value is None:
            groups = period.get("Groups") or []
            s = 0.0
            found = False
            for g in groups:
                if not isinstance(g, dict):
                    continue
                metrics = g.get("Metrics") or {}
                for mv in metrics.values():
                    if isinstance(mv, dict) and "Amount" in mv:
                        amt = _safe_float(mv.get("Amount"))
                        if amt is not None:
                            s += amt
                            found = True
                            break
            if found:
                value = s
        if value is not None and start:
            out.append({"label": str(start)[:32], "value": value})
    return out if out else None


def _normalize_forecast(d: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    frbt = d.get("ForecastResultsByTime")
    if not isinstance(frbt, list) or not frbt:
        return None
    out: list[dict[str, Any]] = []
    for row in frbt:
        if not isinstance(row, dict):
            continue
        tp = row.get("TimePeriod") or {}
        start = tp.get("Start") or ""
        mv = _safe_float(row.get("MeanValue"))
        if mv is not None and start:
            out.append({"label": str(start)[:32], "value": mv})
    return out if out else None


def _normalize_metric_data(d: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
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
    if n == 0:
        return None
    out = []
    for i in range(n):
        v = _safe_float(vals[i])
        if v is None:
            continue
        lab = ts[i]
        if hasattr(lab, "strftime"):
            lab = lab.strftime("%Y-%m-%d %H:%M")
        out.append({"label": str(lab)[:40], "value": v})
    return out if out else None


def _event_to_date_key(ts_ms: Any) -> str:
    try:
        ms = int(ts_ms)
        dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return "unknown"


def _normalize_filter_log_events(d: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    events = d.get("events")
    if not isinstance(events, list) or not events:
        return None
    counts: dict[str, int] = defaultdict(int)
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ts = ev.get("timestamp")
        counts[_event_to_date_key(ts)] += 1
    if not counts or (len(counts) == 1 and "unknown" in counts):
        return None
    out = [{"label": k, "value": float(v)} for k, v in sorted(counts.items()) if k != "unknown"]
    return out if out else None


def _insights_row_to_dict(row: Any) -> dict[str, str]:
    if not isinstance(row, list):
        return {}
    out: dict[str, str] = {}
    for cell in row:
        if isinstance(cell, dict) and "field" in cell and "value" in cell:
            out[str(cell["field"])] = str(cell["value"])
    return out


def _normalize_insights_results(d: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    status = (d.get("status") or "").strip()
    if status in ("Running", "Scheduled", "Failed", "Cancelled"):
        return None
    results = d.get("results")
    if not isinstance(results, list) or not results:
        return None
    rows = [_insights_row_to_dict(r) for r in results if r]
    if not rows:
        return None
    value_keys_priority = (
        "count(*)",
        "count()",
        "Count",
        "cnt",
        "sum(*)",
        "avg(*)",
    )
    sample = rows[0]
    label_key = None
    for k in sample:
        if k.startswith("bin(") or k == "@timestamp":
            label_key = k
            break
    if not label_key:
        for k in ("bin(5m)", "bin(1h)", "bin(1d)", "bin(30m)", "bin(10m)", "bin(1m)"):
            if k in sample:
                label_key = k
                break
    if not label_key:
        label_key = next(iter(sample.keys()), None)
    value_key = None
    for k in value_keys_priority:
        if k in sample:
            value_key = k
            break
    if not value_key:
        for k, v in sample.items():
            if k == label_key:
                continue
            if _safe_float(v) is not None:
                value_key = k
                break
    if not label_key or not value_key:
        return None
    out: list[dict[str, Any]] = []
    for row in rows:
        lab = row.get(label_key, "")
        val = _safe_float(row.get(value_key))
        if val is not None and lab:
            out.append({"label": str(lab)[:48], "value": val})
    return out if out else None


def _try_coerce_plain_list(items: list[Any]) -> Optional[list[dict[str, Any]]]:
    """Already-normalized or simple [{date, value}, ...] arrays."""
    if not items:
        return None
    out: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            return None
        val = (
            _safe_float(row.get("value"))
            or _safe_float(row.get("amount"))
            or _safe_float(row.get("cost"))
            or _safe_float(row.get("total"))
        )
        lab = (
            row.get("label")
            or row.get("date")
            or row.get("name")
            or row.get("period")
            or (row.get("TimePeriod") or {}).get("Start")
        )
        if isinstance(lab, dict):
            lab = lab.get("Start")
        if val is None:
            m = row.get("Metrics")
            if isinstance(m, dict):
                for mv in m.values():
                    if isinstance(mv, dict) and "Amount" in mv:
                        val = _safe_float(mv.get("Amount"))
                        if val is not None:
                            break
        if val is None or lab is None or str(lab).strip() == "":
            return None
        out.append({"label": str(lab)[:80], "value": val})
    return out if len(out) == len(items) else None


def _is_insights_results_table(results: Any) -> bool:
    if not isinstance(results, list) or not results:
        return False
    first = results[0]
    return isinstance(first, list) and first and isinstance(first[0], dict) and "field" in first[0]


def normalize_visualization_input(
    data: str,
    tool_name_hint: Optional[str] = None,
) -> tuple[Optional[list[dict[str, Any]]], str]:
    """
    Convert raw JSON (tool output) into [{label, value}, ...] for charts.

    Returns:
        (series, note): series is None to fall back to legacy parsing; note is for logging/UI.
    """
    _ = tool_name_hint  # reserved for future routing
    if not data or not str(data).strip():
        return None, "empty_input"

    try:
        parsed: Any = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None, "not_json"

    if isinstance(parsed, list):
        coerced = _try_coerce_plain_list(parsed)
        if coerced:
            logger.info("viz_normalizer: plain_list count=%d", len(coerced))
            print(f"[VIZ_NORMALIZER] branch=plain_list points={len(coerced)}", flush=True)
            return _truncate_series(coerced), "plain_list"
        return None, "list_unrecognized_shape"

    if not isinstance(parsed, dict):
        return None, "not_dict_or_list"

    if parsed.get("error") and not any(
        k in parsed
        for k in ("ResultsByTime", "ForecastResultsByTime", "MetricDataResults", "events", "results")
    ):
        return None, "error_payload"

    branches = [
        ("cost_explorer", _normalize_cost_explorer, "ResultsByTime" in parsed),
        ("forecast", _normalize_forecast, "ForecastResultsByTime" in parsed),
        ("metric_data", _normalize_metric_data, "MetricDataResults" in parsed),
        (
            "log_events",
            _normalize_filter_log_events,
            isinstance(parsed.get("events"), list) and bool(parsed.get("events")),
        ),
        (
            "insights",
            _normalize_insights_results,
            _is_insights_results_table(parsed.get("results"))
            and parsed.get("status") not in ("Running", "Scheduled", "Failed", "Cancelled"),
        ),
    ]

    for name, fn, cond in branches:
        if not cond:
            continue
        try:
            series = fn(parsed)
            if series:
                series = _truncate_series(series)
                logger.info("viz_normalizer: branch=%s points=%d", name, len(series))
                print(f"[VIZ_NORMALIZER] branch={name} points={len(series)}", flush=True)
                return series, name
        except Exception as e:
            logger.warning("viz_normalizer: branch %s failed: %s", name, e)

    return None, "no_matching_shape"


def format_normalization_footer(note: str, point_count: int) -> str:
    """Optional one-line hint for the user when data was auto-normalized."""
    if note in (
        "empty_input",
        "not_json",
        "list_unrecognized_shape",
        "not_dict_or_list",
        "error_payload",
        "no_matching_shape",
    ):
        return ""
    return f"\n\n*Chart data was normalized automatically ({note}, {point_count} points).*"


if __name__ == "__main__":
    import json as _json

    def _check(name: str, ok: bool) -> None:
        print(name, "OK" if ok else "FAIL")

    _s, _ = normalize_visualization_input(
        _json.dumps(
            {
                "ResultsByTime": [
                    {
                        "TimePeriod": {"Start": "2026-03-01"},
                        "Groups": [
                            {"Metrics": {"UnblendedCost": {"Amount": "1.5"}}},
                            {"Metrics": {"UnblendedCost": {"Amount": "2.5"}}},
                        ],
                    }
                ]
            }
        )
    )
    _check("cost_groups_sum", _s and _s[0]["value"] == 4.0)
    _s2, _ = normalize_visualization_input(
        _json.dumps(
            {
                "ResultsByTime": [
                    {
                        "TimePeriod": {"Start": "2026-03-02"},
                        "Total": {"UnblendedCost": {"Amount": "9.99"}},
                    }
                ]
            }
        )
    )
    _check("cost_total", _s2 and _s2[0]["value"] == 9.99)
    _dup = {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-03-11"},
                "Groups": [{"Metrics": {"UnblendedCost": {"Amount": "3"}}}],
            },
            {
                "TimePeriod": {"Start": "2026-03-11"},
                "Groups": [{"Metrics": {"UnblendedCost": {"Amount": "5.4"}}}],
            },
        ]
    }
    _s3, _ = normalize_visualization_input(_json.dumps(_dup))
    _check("merge_duplicate_period", _s3 and len(_s3) == 1 and abs(_s3[0]["value"] - 8.4) < 0.01)
    print("self-check done")
