# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Unified Cloud Intelligence Tools Lambda for AgentCore Gateway.
Exposes Cost Explorer, CloudWatch, and CloudTrail tools via a single Lambda target.
Gateway passes event = tool input (inputSchema properties) and context with bedrockAgentCoreToolName = targetId___toolName.
"""

from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import boto3

# Clients (initialized once)
ce = boto3.client("ce")
cloudwatch = boto3.client("cloudwatch")
logs = boto3.client("logs")
cloudtrail = boto3.client("cloudtrail")
lambda_client = boto3.client("lambda")
ecs_client = boto3.client("ecs")
config_client = boto3.client("config")

DELIMITER = "___"


def _get_tool_name(event: dict, context: Any) -> str:
    """Resolve tool name: Gateway sends targetId___toolName in context."""
    name = None
    if context and getattr(context, "client_context", None) and getattr(context.client_context, "custom", None):
        name = context.client_context.custom.get("bedrockAgentCoreToolName")
    if not name and isinstance(event, dict):
        name = event.get("bedrockAgentCoreToolName") or event.get("__toolName")
    if not name:
        return ""
    if DELIMITER in name:
        return name.split(DELIMITER, 1)[-1]
    return name


def _today_date() -> Dict[str, str]:
    now_utc = datetime.now(timezone.utc)
    return {"today_date_UTC": now_utc.strftime("%Y-%m-%d"), "current_month": now_utc.strftime("%Y-%m")}


def _normalize_group_by(group_by: Any) -> Optional[Dict[str, str]]:
    """
    None => omit GroupBy (account totals per period; matches Cost Explorer with no dimension).
    SERVICE, REGION, etc. => group by that dimension.
    """
    if group_by is None:
        return None
    if isinstance(group_by, str):
        g = group_by.strip()
        if not g or g.upper() in ("NONE", "N/A", "TOTAL", "NULL"):
            return None
        return {"Type": "DIMENSION", "Key": g}
    return {"Type": group_by.get("Type", "DIMENSION"), "Key": group_by.get("Key", "SERVICE")}


def _merge_groups_by_keys(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Combine duplicate Keys (same dimension value) by summing metric Amounts."""
    key_order: List[tuple] = []
    merged: Dict[tuple, Dict[str, Any]] = {}
    for g in groups:
        if not isinstance(g, dict):
            continue
        keys_t = tuple(g.get("Keys") or [])
        metrics = g.get("Metrics") or {}
        if keys_t not in merged:
            key_order.append(keys_t)
            merged[keys_t] = {"Keys": list(keys_t), "Metrics": {}}
            for mk, mv in metrics.items():
                if isinstance(mv, dict):
                    merged[keys_t]["Metrics"][mk] = dict(mv)
                else:
                    merged[keys_t]["Metrics"][mk] = mv
            continue
        dest_m = merged[keys_t]["Metrics"]
        for mk, mv in metrics.items():
            if isinstance(mv, dict) and "Amount" in mv:
                try:
                    add = float(mv.get("Amount") or 0)
                except (TypeError, ValueError):
                    add = 0.0
                if mk not in dest_m:
                    dest_m[mk] = dict(mv)
                else:
                    dm = dest_m[mk]
                    if isinstance(dm, dict) and "Amount" in dm:
                        try:
                            base = float(dm.get("Amount") or 0)
                        except (TypeError, ValueError):
                            base = 0.0
                        dm["Amount"] = str(base + add)
            elif mk not in dest_m:
                dest_m[mk] = mv if isinstance(mv, dict) else mv
    return [merged[k] for k in key_order]


def _sum_total_blocks(t1: Dict[str, Any], t2: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(t1)
    for mk, mv in t2.items():
        if isinstance(mv, dict) and "Amount" in mv:
            try:
                a = float((out.get(mk) or {}).get("Amount") or 0) + float(mv.get("Amount") or 0)
            except (TypeError, ValueError):
                a = float(mv.get("Amount") or 0)
            out[mk] = {**mv, "Amount": str(a)}
        elif mk not in out:
            out[mk] = mv
    return out


def _merge_cost_explorer_results_by_time(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Cost Explorer pagination can return multiple ResultsByTime rows for the same
    TimePeriod.Start with different Groups. Merge into one row per period.
    """
    order: List[str] = []
    by_start: Dict[str, Dict[str, Any]] = {}
    for row in results:
        if not isinstance(row, dict):
            continue
        tp = row.get("TimePeriod") or {}
        start = tp.get("Start") or ""
        if not start:
            continue
        groups = row.get("Groups") or []
        total = row.get("Total")
        if start not in by_start:
            order.append(start)
            by_start[start] = {
                "TimePeriod": {"Start": tp.get("Start"), "End": tp.get("End")},
                "Groups": list(groups) if groups else [],
            }
            if total:
                by_start[start]["Total"] = dict(total) if isinstance(total, dict) else total
        else:
            by_start[start]["Groups"].extend(list(groups))
            if total and isinstance(total, dict):
                prev = by_start[start].get("Total")
                if isinstance(prev, dict):
                    by_start[start]["Total"] = _sum_total_blocks(prev, total)
                else:
                    by_start[start]["Total"] = dict(total)
    out: List[Dict[str, Any]] = []
    for start in order:
        block = by_start[start]
        gr = block.get("Groups") or []
        if gr:
            block["Groups"] = _merge_groups_by_keys(gr)
        out.append(block)
    return out


def _adjust_end_date_inclusive(end_date: str) -> str:
    dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def _aggregate_services_for_date_range(
    start_date: str,
    end_date_exclusive: str,
    metric: str,
    filter_expression: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    One MONTHLY-bucketed CE query over the range, GroupBy SERVICE, full pagination + merge.
    Service amounts are scoped to the requested TimePeriod (incl. partial months).
    Sum(by_service) = authoritative period total for narratives vs DAILY+SERVICE under-count.
    """
    params: Dict[str, Any] = {
        "TimePeriod": {"Start": start_date, "End": end_date_exclusive},
        "Granularity": "MONTHLY",
        "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
        "Metrics": [metric],
    }
    if filter_expression:
        params["Filter"] = filter_expression
    all_rows: List[Dict[str, Any]] = []
    next_token = None
    while True:
        p = dict(params)
        if next_token:
            p["NextPageToken"] = next_token
        resp = ce.get_cost_and_usage(**p)
        all_rows.extend(resp.get("ResultsByTime", []))
        next_token = resp.get("NextPageToken")
        if not next_token:
            break
    merged = _merge_cost_explorer_results_by_time(all_rows) if all_rows else []
    by_service: Dict[str, float] = defaultdict(float)
    for row in merged:
        for g in row.get("Groups") or []:
            if not isinstance(g, dict):
                continue
            keys = g.get("Keys") or []
            svc = str(keys[0]) if keys else "Unknown"
            for mv in (g.get("Metrics") or {}).values():
                if isinstance(mv, dict) and "Amount" in mv:
                    try:
                        by_service[svc] += float(mv.get("Amount") or 0)
                    except (TypeError, ValueError):
                        pass
                    break
    total = sum(by_service.values())
    ranked = sorted(by_service.items(), key=lambda x: -x[1])
    return {
        "period_total_usd": round(total, 2),
        "by_service": [{"service": a, "amount_usd": round(b, 4)} for a, b in ranked[:50]],
        "_usage": (
            "Use period_total_usd as the overall cost for start_date through end_date (inclusive). "
            "Use by_service for top cost drivers. Do not infer total from DAILY+SERVICE rows alone."
        ),
    }


# ---------- Cost Explorer ----------
def get_today_date(**kwargs: Any) -> Dict[str, Any]:
    """Get the current date in YYYY-MM-DD and current month in YYYY-MM (UTC). Useful for computing date ranges."""
    return _today_date()


def get_dimension_values(start_date: str, end_date: str, dimension_key: str, **kwargs: Any) -> Dict[str, Any]:
    """Get available dimension values for Cost Explorer (e.g. SERVICE, REGION, LINKED_ACCOUNT). Dates in YYYY-MM-DD."""
    try:
        response = ce.get_dimension_values(
            TimePeriod={"Start": start_date, "End": end_date},
            Dimension=dimension_key,
        )
        return {"dimension": dimension_key, "values": [v["Value"] for v in response.get("DimensionValues", [])]}
    except Exception as e:
        return {"error": str(e)}


def get_tag_values(start_date: str, end_date: str, tag_key: str, **kwargs: Any) -> Dict[str, Any]:
    """Get available tag values for a tag key over a billing period. Dates in YYYY-MM-DD."""
    try:
        response = ce.get_tags(TimePeriod={"Start": start_date, "End": end_date}, TagKey=tag_key)
        return {"tag_key": tag_key, "values": response.get("Tags", [])}
    except Exception as e:
        return {"error": str(e)}


def get_cost_and_usage(
    start_date: str,
    end_date: str,
    granularity: str = "MONTHLY",
    group_by: Optional[Any] = None,
    filter_expression: Optional[Dict[str, Any]] = None,
    metric: str = "UnblendedCost",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Retrieve AWS cost and usage. Always includes _period_service_totals (MONTHLY+SERVICE rollup) for period_total_usd and by_service. Omit group_by for CE-style daily/monthly totals in ResultsByTime."""
    try:
        end_adj = _adjust_end_date_inclusive(end_date)
        gb = _normalize_group_by(group_by)
        params: Dict[str, Any] = {
            "TimePeriod": {"Start": start_date, "End": end_adj},
            "Granularity": granularity.upper(),
            "Metrics": [metric],
        }
        if gb is not None:
            params["GroupBy"] = [{"Type": gb["Type"], "Key": gb["Key"]}]
        if filter_expression:
            params["Filter"] = filter_expression
        result = {"ResultsByTime": [], "NextPageToken": None}
        next_token = None
        while True:
            if next_token:
                params["NextPageToken"] = next_token
            response = ce.get_cost_and_usage(**params)
            result["ResultsByTime"].extend(response.get("ResultsByTime", []))
            next_token = response.get("NextPageToken")
            if not next_token:
                break
        if gb is not None and result["ResultsByTime"]:
            result["ResultsByTime"] = _merge_cost_explorer_results_by_time(result["ResultsByTime"])
        try:
            result["_period_service_totals"] = _aggregate_services_for_date_range(
                start_date, end_adj, metric, filter_expression
            )
        except Exception as agg_err:
            result["_period_service_totals_error"] = str(agg_err)
        if granularity.upper() == "DAILY" and gb is None and result.get("ResultsByTime"):
            ds = 0.0
            for row in result["ResultsByTime"]:
                if not isinstance(row, dict):
                    continue
                tot = row.get("Total") or {}
                for mv in tot.values() if isinstance(tot, dict) else []:
                    if isinstance(mv, dict) and "Amount" in mv:
                        try:
                            ds += float(mv.get("Amount") or 0)
                        except (TypeError, ValueError):
                            pass
                        break
            result["_daily_totals_sum_usd"] = round(ds, 2)
        return result
    except Exception as e:
        return {"error": str(e)}


def get_cost_and_usage_comparisons(
    base_start_date: str,
    base_end_date: str,
    comparison_start_date: str,
    comparison_end_date: str,
    granularity: str = "MONTHLY",
    group_by: Optional[Any] = None,
    filter_expression: Optional[Dict[str, Any]] = None,
    metric: str = "UnblendedCost",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Compare costs between two periods. Use full calendar months."""
    try:
        gb = _normalize_group_by(group_by) or {"Type": "DIMENSION", "Key": "SERVICE"}
        params = {
            "Granularity": granularity.upper(),
            "GroupBy": [{"Type": gb["Type"], "Key": gb["Key"]}],
            "Metric": metric,
            "BaseTimePeriod": {"Start": base_start_date, "End": base_end_date},
            "ComparisonTimePeriod": {"Start": comparison_start_date, "End": comparison_end_date},
        }
        if filter_expression:
            params["Filter"] = filter_expression
        return ce.get_cost_and_usage_comparisons(**params)
    except Exception as e:
        return {"error": str(e)}


def get_cost_comparison_drivers(
    base_start_date: str,
    base_end_date: str,
    comparison_start_date: str,
    comparison_end_date: str,
    filter_expression: Optional[Dict[str, Any]] = None,
    metric: str = "UnblendedCost",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Get top cost change drivers between two periods. Returns up to 10 most significant drivers."""
    try:
        params = {
            "Metric": metric,
            "BaseTimePeriod": {"Start": base_start_date, "End": base_end_date},
            "ComparisonTimePeriod": {"Start": comparison_start_date, "End": comparison_end_date},
        }
        if filter_expression:
            params["Filter"] = filter_expression
        return ce.get_cost_comparison_drivers(**params)
    except Exception as e:
        return {"error": str(e)}


def get_cost_forecast(
    start_date: str,
    end_date: str,
    metric: str = "UnblendedCost",
    granularity: str = "MONTHLY",
    filter_expression: Optional[Dict[str, Any]] = None,
    prediction_interval_level: int = 80,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Get cost forecast for a future period. prediction_interval_level: 80 or 95."""
    try:
        params = {
            "TimePeriod": {"Start": start_date, "End": end_date},
            "Metric": metric,
            "Granularity": granularity.upper(),
            "PredictionIntervalLevel": prediction_interval_level,
        }
        if filter_expression:
            params["Filter"] = filter_expression
        return ce.get_cost_forecast(**params)
    except Exception as e:
        return {"error": str(e)}


# ---------- CloudWatch ----------
def get_metric_data(
    namespace: str,
    metric_name: str,
    start_time: str,
    end_time: str,
    dimensions: Optional[List[Dict[str, str]]] = None,
    period: int = 300,
    statistic: str = "Average",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Retrieve CloudWatch metric data. dimensions: list of {Name, Value}. start_time/end_time: ISO8601 or Unix timestamp."""
    try:
        metric_query = {
            "Id": "m1",
            "MetricStat": {
                "Metric": {"Namespace": namespace, "MetricName": metric_name, "Dimensions": dimensions or []},
                "Period": period,
                "Stat": statistic,
            },
        }
        return cloudwatch.get_metric_data(
            MetricDataQueries=[metric_query],
            StartTime=start_time,
            EndTime=end_time,
        )
    except Exception as e:
        return {"error": str(e)}


def get_metric_metadata(
    namespace: str,
    metric_name: str,
    dimensions: Optional[List[Dict[str, str]]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Retrieve metadata about a CloudWatch metric (describe via list_metrics)."""
    try:
        params = {"Namespace": namespace, "MetricName": metric_name}
        if dimensions:
            params["Dimensions"] = dimensions
        return cloudwatch.list_metrics(**params)
    except Exception as e:
        return {"error": str(e)}


def get_recommended_metric_alarms(
    namespace: str,
    metric_name: str,
    dimensions: Optional[List[Dict[str, str]]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Get recommended alarm configurations for a metric (describe alarms for metric)."""
    try:
        params = {"AlarmTypes": ["MetricAlarm"], "MetricName": metric_name, "Namespace": namespace}
        if dimensions:
            params["Dimensions"] = dimensions
        return cloudwatch.describe_alarms(**params)
    except Exception as e:
        return {"error": str(e)}


def analyze_metric(
    namespace: str,
    metric_name: str,
    start_time: str,
    end_time: str,
    dimensions: Optional[List[Dict[str, str]]] = None,
    period: int = 300,
    statistic: str = "Average",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Retrieve metric data for analysis (same as get_metric_data, returns raw data for trend/statistical analysis)."""
    return get_metric_data(
        namespace=namespace,
        metric_name=metric_name,
        start_time=start_time,
        end_time=end_time,
        dimensions=dimensions,
        period=period,
        statistic=statistic,
    )


def get_active_alarms(
    state_value: str = "ALARM",
    alarm_names: Optional[List[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Get currently active CloudWatch alarms. state_value: ALARM, OK, INSUFFICIENT_DATA."""
    try:
        params = {"StateValue": state_value}
        if alarm_names:
            params["AlarmNames"] = alarm_names
        return cloudwatch.describe_alarms(**params)
    except Exception as e:
        return {"error": str(e)}


def get_alarm_history(
    start_date: str,
    end_date: str,
    alarm_name: Optional[str] = None,
    max_records: int = 50,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Retrieve alarm state change history. start_date/end_date: ISO8601."""
    try:
        params = {"StartDate": start_date, "EndDate": end_date, "MaxRecords": max_records}
        if alarm_name:
            params["AlarmName"] = alarm_name
        return cloudwatch.describe_alarm_history(**params)
    except Exception as e:
        return {"error": str(e)}


def describe_log_groups(
    log_group_name_prefix: Optional[str] = None,
    limit: int = 50,
    **kwargs: Any,
) -> Dict[str, Any]:
    """List CloudWatch log groups. Optional prefix filter."""
    try:
        params = {"limit": limit}
        if log_group_name_prefix:
            params["logGroupNamePrefix"] = log_group_name_prefix
        return logs.describe_log_groups(**params)
    except Exception as e:
        return {"error": str(e)}


def describe_log_group(
    log_group_name: str,
    include_streams: bool = True,
    streams_limit: int = 20,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Describe a single CloudWatch log group (details + optional recent log streams). Use to get ARN, stored bytes, stream count, and stream list."""
    try:
        out = logs.describe_log_groups(logGroupNamePrefix=log_group_name, limit=1)
        groups = out.get("logGroups", [])
        if not groups or groups[0].get("logGroupName") != log_group_name:
            return {"error": f"Log group not found: {log_group_name}"}
        result = {**groups[0], "logGroupName": groups[0]["logGroupName"]}
        if include_streams:
            streams = logs.describe_log_streams(
                logGroupName=log_group_name,
                orderBy="LastEventTime",
                descending=True,
                limit=streams_limit,
            )
            result["logStreams"] = streams.get("logStreams", [])
        return result
    except Exception as e:
        return {"error": str(e)}


def analyze_log_group(
    log_group_name: str,
    start_time: int,
    end_time: int,
    filter_pattern: Optional[str] = None,
    limit: int = 100,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Filter and retrieve log events from a log group for analysis. start_time/end_time: Unix ms."""
    try:
        params = {"logGroupName": log_group_name, "startTime": start_time, "endTime": end_time, "limit": limit}
        if filter_pattern:
            params["filterPattern"] = filter_pattern
        return logs.filter_log_events(**params)
    except Exception as e:
        return {"error": str(e)}


def execute_log_insights_query(
    log_group_name: str,
    query_string: str,
    start_time: int,
    end_time: int,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Start a CloudWatch Logs Insights query. Returns query ID; use get_logs_insight_query_results to fetch results. start_time/end_time: Unix seconds."""
    try:
        response = logs.start_query(
            logGroupName=log_group_name,
            startTime=start_time,
            endTime=end_time,
            queryString=query_string,
        )
        return {"queryId": response["queryId"]}
    except Exception as e:
        return {"error": str(e)}


def get_logs_insight_query_results(query_id: str, **kwargs: Any) -> Dict[str, Any]:
    """Get results of a CloudWatch Logs Insights query by query ID."""
    try:
        return logs.get_query_results(queryId=query_id)
    except Exception as e:
        return {"error": str(e)}


def cancel_logs_insight_query(query_id: str, **kwargs: Any) -> Dict[str, Any]:
    """Cancel a running CloudWatch Logs Insights query."""
    try:
        logs.stop_query(queryId=query_id)
        return {"status": "cancelled", "queryId": query_id}
    except Exception as e:
        return {"error": str(e)}


# ---------- Lambda (discovery) ----------
def list_lambda_functions(
    function_version: str = "ALL",
    max_items: int = 50,
    name_prefix: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """List Lambda functions in the account. Optional name_prefix to filter by function name prefix."""
    try:
        params = {"MaxItems": max_items, "FunctionVersion": function_version}
        response = lambda_client.list_functions(**params)
        funcs = response.get("Functions", [])
        if name_prefix:
            prefix_lower = name_prefix.lower()
            funcs = [f for f in funcs if (f.get("FunctionName") or "").lower().startswith(prefix_lower)]
        return {"Functions": funcs[:max_items], "Count": len(funcs)}
    except Exception as e:
        return {"error": str(e)}


def describe_lambda_function(function_name: str, **kwargs: Any) -> Dict[str, Any]:
    """Get details of a Lambda function (config, runtime, env, last modified, etc.)."""
    try:
        return lambda_client.get_function(FunctionName=function_name)
    except Exception as e:
        return {"error": str(e)}


# ---------- ECS (discovery) ----------
def list_ecs_clusters(max_results: int = 100, **kwargs: Any) -> Dict[str, Any]:
    """List ECS clusters in the account."""
    try:
        response = ecs_client.list_clusters(maxResults=max_results)
        cluster_arns = response.get("clusterArns", [])
        if not cluster_arns:
            return {"clusters": [], "clusterArns": []}
        desc = ecs_client.describe_clusters(clusters=cluster_arns)
        return {"clusters": desc.get("clusters", []), "clusterArns": cluster_arns}
    except Exception as e:
        return {"error": str(e)}


def list_ecs_services(
    cluster: str,
    max_results: int = 100,
    **kwargs: Any,
) -> Dict[str, Any]:
    """List ECS services in a cluster. cluster: cluster name or ARN."""
    try:
        response = ecs_client.list_services(cluster=cluster, maxResults=max_results)
        service_arns = response.get("serviceArns", [])
        if not service_arns:
            return {"services": [], "serviceArns": []}
        desc = ecs_client.describe_services(cluster=cluster, services=service_arns)
        return {"services": desc.get("services", []), "serviceArns": service_arns}
    except Exception as e:
        return {"error": str(e)}


def describe_ecs_service(
    cluster: str,
    services: List[str],
    **kwargs: Any,
) -> Dict[str, Any]:
    """Describe one or more ECS services. services: list of service names or ARNs."""
    try:
        if isinstance(services, str):
            services = [services]
        return ecs_client.describe_services(cluster=cluster, services=services)
    except Exception as e:
        return {"error": str(e)}


# ---------- AWS Config (discovery) ----------
def list_discovered_resources(
    resource_type: str,
    resource_name: Optional[str] = None,
    limit: int = 100,
    next_token: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """List discovered resources by type (AWS Config). resource_type: e.g. AWS::EC2::Instance, AWS::Lambda::Function, AWS::ECS::Cluster. Use to see what resources exist in the account."""
    try:
        params = {"resourceType": resource_type, "limit": limit}
        if resource_name:
            params["resourceName"] = resource_name
        if next_token:
            params["nextToken"] = next_token
        response = config_client.list_discovered_resources(**params)
        return {
            "resourceIdentifiers": response.get("resourceIdentifiers", []),
            "nextToken": response.get("nextToken"),
        }
    except Exception as e:
        return {"error": str(e)}


def describe_configuration_recorders(**kwargs: Any) -> Dict[str, Any]:
    """Describe AWS Config configuration recorders (whether Config is recording and for which resource types). Use to see if Config is enabled and what it records."""
    try:
        return config_client.describe_configuration_recorders()
    except Exception as e:
        return {"error": str(e)}


# ---------- CloudTrail ----------
def lookup_events(
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    lookup_attributes: Optional[List[Dict[str, str]]] = None,
    max_results: int = 50,
    next_token: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Look up CloudTrail management events (last 90 days). lookup_attributes: list of {AttributeKey, AttributeValue} e.g. EventName, Username, ResourceName."""
    try:
        params = {"MaxResults": max_results}
        if start_time:
            params["StartTime"] = start_time
        if end_time:
            params["EndTime"] = end_time
        if lookup_attributes:
            params["LookupAttributes"] = lookup_attributes
        if next_token:
            params["NextToken"] = next_token
        return cloudtrail.lookup_events(**params)
    except Exception as e:
        return {"error": str(e)}


def lake_query(query_statement: str, **kwargs: Any) -> Dict[str, Any]:
    """Start a CloudTrail Lake SQL query (Trino-compatible). Returns query ID; use get_query_status/get_query_results."""
    try:
        response = cloudtrail.start_query(QueryStatement=query_statement)
        return {"QueryId": response["QueryId"]}
    except Exception as e:
        return {"error": str(e)}


def list_event_data_stores(
    max_results: int = 50,
    next_token: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """List CloudTrail Lake event data stores."""
    try:
        params = {"MaxResults": max_results}
        if next_token:
            params["NextToken"] = next_token
        return cloudtrail.list_event_data_stores(**params)
    except Exception as e:
        return {"error": str(e)}


def get_query_status(query_id: str, **kwargs: Any) -> Dict[str, Any]:
    """Get status of a CloudTrail Lake query (QUEUED, RUNNING, FINISHED, FAILED, CANCELLED, TIMED_OUT)."""
    try:
        return cloudtrail.describe_query(QueryId=query_id)
    except Exception as e:
        return {"error": str(e)}


def get_query_results(
    query_id: str,
    next_token: Optional[str] = None,
    max_query_results: int = 1000,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Get results of a completed CloudTrail Lake query. Paginate with next_token."""
    try:
        params = {"QueryId": query_id, "MaxQueryResults": max_query_results}
        if next_token:
            params["NextToken"] = next_token
        return cloudtrail.get_query_results(**params)
    except Exception as e:
        return {"error": str(e)}


# ---------- User-defined: list available tools ----------
AVAILABLE_TOOLS_DESCRIPTIONS = [
    {"name": "list_available_tools", "description": "List all tools at your disposal (this tool). Use when the user asks what tools they have, what they can do, or what capabilities are available."},
    {"name": "get_today_date", "description": "Get current date in YYYY-MM-DD and current month (UTC). Useful for cost/usage date ranges."},
    {"name": "get_dimension_values", "description": "Get Cost Explorer dimension values (e.g. SERVICE, REGION, LINKED_ACCOUNT)."},
    {"name": "get_tag_values", "description": "Get tag values for a tag key over a billing period."},
    {"name": "get_cost_and_usage", "description": "Retrieve AWS cost and usage data by date range, granularity, and group-by dimension."},
    {"name": "get_cost_and_usage_comparisons", "description": "Compare costs between two time periods."},
    {"name": "get_cost_comparison_drivers", "description": "Get top cost change drivers between two periods."},
    {"name": "get_cost_forecast", "description": "Get cost forecast for a future period."},
    {"name": "get_metric_data", "description": "Retrieve CloudWatch metric data."},
    {"name": "get_metric_metadata", "description": "Get CloudWatch metric metadata (list_metrics)."},
    {"name": "get_recommended_metric_alarms", "description": "Get alarm configurations for a metric."},
    {"name": "analyze_metric", "description": "Retrieve CloudWatch metric data for analysis."},
    {"name": "get_active_alarms", "description": "Get active CloudWatch alarms (ALARM, OK, INSUFFICIENT_DATA)."},
    {"name": "get_alarm_history", "description": "Get CloudWatch alarm state change history."},
    {"name": "describe_log_groups", "description": "List CloudWatch log groups."},
    {"name": "describe_log_group", "description": "Describe a single log group (details and recent streams)."},
    {"name": "analyze_log_group", "description": "Filter and retrieve log events from a log group."},
    {"name": "execute_log_insights_query", "description": "Start a CloudWatch Logs Insights query."},
    {"name": "get_logs_insight_query_results", "description": "Get results of a Logs Insights query by query ID."},
    {"name": "cancel_logs_insight_query", "description": "Cancel a running Logs Insights query."},
    {"name": "list_lambda_functions", "description": "List Lambda functions in the account."},
    {"name": "describe_lambda_function", "description": "Get details of a Lambda function."},
    {"name": "list_ecs_clusters", "description": "List ECS clusters in the account."},
    {"name": "list_ecs_services", "description": "List ECS services in a cluster."},
    {"name": "describe_ecs_service", "description": "Describe ECS service(s) in a cluster."},
    {"name": "list_discovered_resources", "description": "List AWS Config discovered resources by type (e.g. AWS::Lambda::Function, AWS::ECS::Cluster)."},
    {"name": "describe_configuration_recorders", "description": "Describe AWS Config configuration recorders (whether Config is recording)."},
    {"name": "lookup_events", "description": "Look up CloudTrail management events (last 90 days)."},
    {"name": "lake_query", "description": "Start a CloudTrail Lake SQL query (Trino)."},
    {"name": "list_event_data_stores", "description": "List CloudTrail Lake event data stores."},
    {"name": "get_query_status", "description": "Get status of a CloudTrail Lake query."},
    {"name": "get_query_results", "description": "Get results of a completed CloudTrail Lake query."},
]


def list_available_tools(**kwargs: Any) -> Dict[str, Any]:
    """Return the list of all tools available at your disposal. Use when the user asks what tools they have, what they can do, or what capabilities are available."""
    return {
        "tools": AVAILABLE_TOOLS_DESCRIPTIONS,
        "summary": f"You have {len(AVAILABLE_TOOLS_DESCRIPTIONS)} tools: 1 meta, 7 Cost Explorer, 11 CloudWatch (logs/metrics), 8 discovery (Lambda/ECS/Config + describe_log_group), 5 CloudTrail. Use list_available_tools to get this list, or ask for cost, metrics, logs, audit, or discovery.",
    }


# Dispatcher: tool name -> handler
TOOL_HANDLERS = {
    "list_available_tools": list_available_tools,
    "get_today_date": get_today_date,
    "get_dimension_values": get_dimension_values,
    "get_tag_values": get_tag_values,
    "get_cost_and_usage": get_cost_and_usage,
    "get_cost_and_usage_comparisons": get_cost_and_usage_comparisons,
    "get_cost_comparison_drivers": get_cost_comparison_drivers,
    "get_cost_forecast": get_cost_forecast,
    "get_metric_data": get_metric_data,
    "get_metric_metadata": get_metric_metadata,
    "get_recommended_metric_alarms": get_recommended_metric_alarms,
    "analyze_metric": analyze_metric,
    "get_active_alarms": get_active_alarms,
    "get_alarm_history": get_alarm_history,
    "describe_log_groups": describe_log_groups,
    "describe_log_group": describe_log_group,
    "analyze_log_group": analyze_log_group,
    "execute_log_insights_query": execute_log_insights_query,
    "get_logs_insight_query_results": get_logs_insight_query_results,
    "cancel_logs_insight_query": cancel_logs_insight_query,
    "list_lambda_functions": list_lambda_functions,
    "describe_lambda_function": describe_lambda_function,
    "list_ecs_clusters": list_ecs_clusters,
    "list_ecs_services": list_ecs_services,
    "describe_ecs_service": describe_ecs_service,
    "list_discovered_resources": list_discovered_resources,
    "describe_configuration_recorders": describe_configuration_recorders,
    "lookup_events": lookup_events,
    "lake_query": lake_query,
    "list_event_data_stores": list_event_data_stores,
    "get_query_status": get_query_status,
    "get_query_results": get_query_results,
}


def lambda_handler(event: dict, context: Any) -> dict:
    """Route Gateway invocation to the correct tool by name. Event = tool input (inputSchema properties)."""
    tool_name = _get_tool_name(event, context)
    if not tool_name:
        return {"error": "Missing bedrockAgentCoreToolName in context or event"}
    handler = TOOL_HANDLERS.get(tool_name)
    if not handler:
        return {"error": f"Unknown tool: {tool_name}"}
    # Event is the map of inputSchema properties; pass as kwargs (strip internal keys)
    kwargs = {k: v for k, v in event.items() if not k.startswith("__") and k != "bedrockAgentCoreToolName"}
    # Log tool name and key cost args for debugging (CloudWatch)
    print(f"[LAMBDA] tool={tool_name} kwargs_keys={list(kwargs.keys())}", flush=True)
    if tool_name == "get_cost_and_usage":
        print(
            f"[LAMBDA] get_cost_and_usage start_date={kwargs.get('start_date')} end_date={kwargs.get('end_date')} granularity={kwargs.get('granularity')} group_by={kwargs.get('group_by')}",
            flush=True,
        )
    elif tool_name == "get_cost_forecast":
        print(
            f"[LAMBDA] get_cost_forecast start_date={kwargs.get('start_date')} end_date={kwargs.get('end_date')}",
            flush=True,
        )
    try:
        result = handler(**kwargs)
        return result if isinstance(result, dict) else {"result": result}
    except Exception as e:
        print(f"[LAMBDA] error tool={tool_name} error={e}", flush=True)
        return {"error": str(e)}
