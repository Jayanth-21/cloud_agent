---
id: log-exploration
name: Log Exploration
description: >
  CloudWatch logs, metrics, alarms, and Logs Insights—finding errors, noisy log groups,
  metric spikes, alarm history, and running queries over log data. Use when the user
  mentions logs, log groups, Insights, metrics, namespaces, or alarms—not raw Cost Explorer.
routing_body_chars: 900
tools:
  - get_metric_data
  - get_metric_metadata
  - get_recommended_metric_alarms
  - analyze_metric
  - get_active_alarms
  - get_alarm_history
  - describe_log_groups
  - describe_log_group
  - analyze_log_group
  - execute_log_insights_query
  - get_logs_insight_query_results
  - cancel_logs_insight_query
---

## Playbook

- Narrow **log group** scope when possible; use **describe_log_groups** / **describe_log_group** to discover names.
- For ad-hoc investigation, **execute_log_insights_query** with a time window; poll **get_logs_insight_query_results** as needed.
- For performance / saturation questions, use **get_metric_data** with correct namespace and dimensions.
- Use **analyze_log_group** or **analyze_metric** when the tool descriptions indicate they fit the question.
