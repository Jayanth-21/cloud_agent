# Unified Cloud Intelligence Tools Lambda

Single Lambda that exposes **Cost Explorer**, **CloudWatch**, and **CloudTrail** tools for the AgentCore Gateway. Add this Lambda as one Gateway target (no API Gateway or MCP servers).

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
   - Execution role: permissions for `ce:*`, `cloudwatch:*`, `logs:*`, `cloudtrail:*` (read-only as needed)

3. **Add the Lambda as a Gateway target** in AgentCore:
   - In **Bedrock → AgentCore → Gateways → your gateway → Targets**, add target type **Lambda**.
   - **Target name**: e.g. `unified-tools`
   - **Lambda ARN**: your function ARN (e.g. `arn:aws:lambda:us-east-1:123456789012:function:unified-tools`)
   - **Tool schema**: choose **Inline** and paste the contents of `gateway_inline_schema.json` (the full JSON array of tool definitions).

4. **Gateway service role**: ensure the Gateway’s role can invoke this Lambda (`lambda:InvokeFunction` on the function ARN).

5. **Synchronize** the target in the Gateway console.

## Inline schema (copy-paste)

Use the JSON array in **`gateway_inline_schema.json`** as the inline tool schema when adding the Lambda target. It defines all 24 tools (Cost Explorer, CloudWatch, CloudTrail) with names, descriptions, and input schemas.

## Agent

The agent’s `scoping/domains.py` already lists the same tool names (`get_cost_and_usage`, `get_metric_data`, `lookup_events`, etc.). No agent code change is required; point `GATEWAY_MCP_URL` at your Gateway and the agent will receive tools from this single target (with optional `targetId___toolName` prefix; the agent strips it).
