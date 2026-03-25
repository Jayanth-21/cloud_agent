# Unified Cloud Intelligence Tools Lambda

Single Lambda that exposes **Cost Explorer**, **CloudWatch**, **CloudTrail**, and **discovery** (Lambda, ECS, AWS Config, log groups) tools for the AgentCore Gateway. Add this Lambda as one Gateway target (no API Gateway or MCP servers).

## Deploy the Lambda

1. **Package** (from this directory):
   ```bash
   pip install -r requirements.txt -t package
   cd package && zip -r ../unified_tools.zip . && cd ..
   zip -g unified_tools.zip handler.py
   ```

2. **Create the function** in AWS Lambda (Console or CLI):
   - Runtime: Python 3.11 or 3.12
   - Handler: `handler.lambda_handler`
   - Timeout: 60+ seconds recommended
   - Execution role: use the policy in **`iam-policy-unified-tools.json`** (Cost Explorer, CloudWatch, CloudTrail, **Lambda**, **ECS**, **Config** read-only).

3. **Add the Lambda as a Gateway target** in AgentCore:
   - In **Bedrock → AgentCore → Gateways → your gateway → Targets**, add target type **Lambda**.
   - **Target name**: e.g. `unified-tools`
   - **Lambda ARN**: your function ARN (e.g. `arn:aws:lambda:us-east-1:123456789012:function:unified-tools`)
   - **Tool schema**: choose **Inline** and paste the contents of **`../gateway_inline_schema.json`** (the full JSON array of tool definitions).

4. **Gateway service role**: ensure the Gateway’s role can invoke this Lambda (`lambda:InvokeFunction` on the function ARN).

5. **Synchronize** the target in the Gateway console. After adding new tools, update the inline schema in the Gateway to the latest `gateway_inline_schema.json` and sync again.

## Inline schema (copy-paste)

Use the JSON array in **`../gateway_inline_schema.json`** (repo path `lambdas/gateway_inline_schema.json`) as the inline tool schema when adding or updating the Lambda target. It defines all tools: Cost Explorer, CloudWatch (metrics/logs), CloudTrail, and discovery (describe_log_group, list_lambda_functions, describe_lambda_function, list_ecs_clusters, list_ecs_services, describe_ecs_service, list_discovered_resources, describe_configuration_recorders).

## Agent

The agent’s `agent/src/scoping/domains.py` maps tool names to domains (cost, logs, audit, discovery, all). Discovery tools are used when the user asks about log groups, Lambdas, ECS, or AWS Config. Point `GATEWAY_MCP_URL` at your Gateway and the agent will receive tools from this single target (with optional `targetId___toolName` prefix; the agent strips it).
