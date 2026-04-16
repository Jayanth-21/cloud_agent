---
id: cost-analysis
name: Cost Analysis
description: >
  AWS billing and Cost Explorer workflows—spend by service, region, account, tags,
  forecasts, period comparisons, budgets, and anomaly-style drivers. Use for questions
  about how much things cost, forecasts, CE dimensions, and FinOps summaries.
routing_body_chars: 900
tools:
  - get_today_date
  - get_dimension_values
  - get_tag_values
  - get_cost_and_usage
  - get_cost_and_usage_comparisons
  - get_cost_comparison_drivers
  - get_cost_forecast
---

## Playbook

- Prefer **get_today_date** when the user uses relative ranges ("last 7 days", "this month").
- For totals and trends, use **get_cost_and_usage** with explicit `start_date` / `end_date` in `YYYY-MM-DD`.
- For overall period spend, omit `group_by` or use totals guidance from tool docs; use `_period_service_totals` in responses when present.
- Use **get_cost_forecast** only when the user asks about future spend.
- Use **get_cost_and_usage_comparisons** / **get_cost_comparison_drivers** for period-over-period questions.
