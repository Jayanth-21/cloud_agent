# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Unified Cloud Intelligence Tools Lambda for AgentCore Gateway.
Exposes Cost Explorer, CloudWatch, and CloudTrail tools via a single Lambda target.
Gateway passes event = tool input (inputSchema properties) and context with bedrockAgentCoreToolName = targetId___toolName.
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import boto3

# Clients (initialized once)
ce = boto3.client("ce")
cloudwatch = boto3.client("cloudwatch")
logs = boto3.client("logs")
cloudtrail = boto3.client("cloudtrail")

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


def _normalize_group_by(group_by: Any) -> Dict[str, str]:
    if group_by is None:
        return {"Type": "DIMENSION", "Key": "SERVICE"}
    if isinstance(group_by, str):
        return {"Type": "DIMENSION", "Key": group_by}
    return {"Type": group_by.get("Type", "DIMENSION"), "Key": group_by.get("Key", "SERVICE")}


def _adjust_end_date_inclusive(end_date: str) -> str:
    dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


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
    """Retrieve AWS cost and usage data. Dates YYYY-MM-DD (end_date inclusive). granularity: DAILY|MONTHLY|HOURLY. group_by: e.g. SERVICE. metric: UnblendedCost, BlendedCost, UsageQuantity."""
    try:
        end_adj = _adjust_end_date_inclusive(end_date)
        gb = _normalize_group_by(group_by)
        params = {
            "TimePeriod": {"Start": start_date, "End": end_adj},
            "Granularity": granularity.upper(),
            "GroupBy": [{"Type": gb["Type"], "Key": gb["Key"]}],
            "Metrics": [metric],
        }
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
        gb = _normalize_group_by(group_by)
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
    {"name": "analyze_log_group", "description": "Filter and retrieve log events from a log group."},
    {"name": "execute_log_insights_query", "description": "Start a CloudWatch Logs Insights query."},
    {"name": "get_logs_insight_query_results", "description": "Get results of a Logs Insights query by query ID."},
    {"name": "cancel_logs_insight_query", "description": "Cancel a running Logs Insights query."},
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
        "summary": f"You have {len(AVAILABLE_TOOLS_DESCRIPTIONS)} tools: 1 meta tool (list_available_tools), 7 Cost Explorer, 11 CloudWatch, and 5 CloudTrail tools. Use list_available_tools to get this list, or ask for cost, metrics, logs, or audit data.",
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
    "analyze_log_group": analyze_log_group,
    "execute_log_insights_query": execute_log_insights_query,
    "get_logs_insight_query_results": get_logs_insight_query_results,
    "cancel_logs_insight_query": cancel_logs_insight_query,
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
    try:
        # Event is the map of inputSchema properties; pass as kwargs (strip internal keys)
        kwargs = {k: v for k, v in event.items() if not k.startswith("__") and k != "bedrockAgentCoreToolName"}
        result = handler(**kwargs)
        return result if isinstance(result, dict) else {"result": result}
    except Exception as e:
        return {"error": str(e)}
