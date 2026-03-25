# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tool scoping: Cost, Logs, Audit, Discovery domains. Gateway tools from Lambda MCP targets."""

from typing import Any, Literal

Domain = Literal["cost", "logs", "audit", "discovery", "all"]

# Tool names from Lambda target (Cost Explorer, CloudWatch, CloudTrail, discovery + meta).
# Gateway may prefix with target id (e.g. unified-tools___get_cost_and_usage). We match by suffix.
# Meta tool: always included so the agent can answer "what tools do I have?"
META_TOOLS = {"list_available_tools"}
COST_TOOLS = {
    "get_today_date",
    "get_dimension_values",
    "get_tag_values",
    "get_cost_and_usage",
    "get_cost_and_usage_comparisons",
    "get_cost_comparison_drivers",
    "get_cost_forecast",
}
LOGS_TOOLS = {
    "get_metric_data",
    "get_metric_metadata",
    "get_recommended_metric_alarms",
    "analyze_metric",
    "get_active_alarms",
    "get_alarm_history",
    "describe_log_groups",
    "describe_log_group",
    "analyze_log_group",
    "execute_log_insights_query",
    "get_logs_insight_query_results",
    "cancel_logs_insight_query",
}
AUDIT_TOOLS = {
    "lookup_events",
    "lake_query",
    "list_event_data_stores",
    "get_query_status",
    "get_query_results",
}
# Discovery: list/describe services, log groups, Lambda, ECS, AWS Config (what's deployed on the account).
DISCOVERY_TOOLS = {
    "describe_log_groups",
    "describe_log_group",
    "list_lambda_functions",
    "describe_lambda_function",
    "list_ecs_clusters",
    "list_ecs_services",
    "describe_ecs_service",
    "list_discovered_resources",
    "describe_configuration_recorders",
}


def _tool_name_base(name: str) -> str:
    """Return base tool name if Gateway uses TargetId___ToolName."""
    if "___" in name:
        return name.split("___")[-1]
    return name


def filter_tools_by_domain(tools: list[Any], domain: Domain) -> list[Any]:
    """
    Return tools that belong to the given domain.
    tools: list of LangChain tools (from Gateway tools/list).
    domain: "cost" | "logs" | "audit" | "discovery" | "all".
    """
    if domain == "all":
        return list(tools)
    if domain == "cost":
        allowed = COST_TOOLS | META_TOOLS
    elif domain == "logs":
        allowed = LOGS_TOOLS | META_TOOLS
    elif domain == "audit":
        allowed = AUDIT_TOOLS | META_TOOLS
    elif domain == "discovery":
        allowed = DISCOVERY_TOOLS | META_TOOLS
    else:
        return list(tools)
    return [t for t in tools if _tool_name_base(getattr(t, "name", "")) in allowed]


def infer_domain_from_message(message: str) -> Domain:
    """
    Simple heuristic: infer which domain the user is asking about.
    Returns "all" if unclear or mixed.
    """
    lower = (message or "").lower()
    cost_keywords = ("cost", "spend", "billing", "budget", "forecast", "usage", "ce:")
    logs_keywords = ("log", "metric", "alarm", "cloudwatch", "insight")
    audit_keywords = ("cloudtrail", "audit", "event", "who did", "api call", "lake")
    discovery_keywords = (
        "list log group", "log groups", "what services", "what do i have", "list lambda",
        "list lambdas", "describe lambda", "ecs cluster", "ecs service", "list ecs",
        "config", "discovery", "what's deployed", "resources in my account", "list resources",
    )
    has_cost = any(k in lower for k in cost_keywords)
    has_logs = any(k in lower for k in logs_keywords)
    has_audit = any(k in lower for k in audit_keywords)
    has_discovery = any(k in lower for k in discovery_keywords)
    if has_cost and not has_logs and not has_audit and not has_discovery:
        return "cost"
    if has_logs and not has_cost and not has_audit and not has_discovery:
        return "logs"
    if has_audit and not has_cost and not has_logs and not has_discovery:
        return "audit"
    if has_discovery and not has_cost and not has_logs and not has_audit:
        return "discovery"
    return "all"
