#!/usr/bin/env python3
"""
Invoke the deployed AgentCore runtime (cloudAgent) with a JSON payload.

Supports:
- Streaming: when the runtime returns contentType "text/event-stream", responses
  are streamed (iter_lines with "data: " prefix). Otherwise response is read and
  parsed as JSON.
- Session: pass "session_id" in the payload (or sessionId from UI) to reuse
  runtimeSessionId for conversation memory.

What you can use this agent for (when Gateway tools are synced):
  - Cost: "What are my costs this month?", "Show spend by service", "Cost forecast"
  - Logs: CloudWatch log queries (scope: "logs")
  - Audit: CloudTrail events (scope: "audit")
  - Scope is inferred from the prompt; pass "scope": "cost"|"logs"|"audit"|"all" to override.

Usage:
  python invoke_agent.py
      → asks "What are my costs this month?" (cost scope)
  python invoke_agent.py What are my costs this month?
  python invoke_agent.py "Show cost by service"
  python invoke_agent.py '{"prompt": "List cost tools", "scope": "cost", "session_id": "optional-uuid"}'
"""
import json
import sys
import uuid

import boto3
import yaml
from botocore.config import Config

CONFIG_PATH = ".bedrock_agentcore.yaml"


def get_agent_arn_and_region():
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    default = config.get("default_agent")
    if not default or default not in config.get("agents", {}):
        raise SystemExit("default_agent not found in .bedrock_agentcore.yaml")
    agent = config["agents"][default]
    arn = agent.get("bedrock_agentcore", {}).get("agent_arn")
    region = agent.get("aws", {}).get("region")
    if not arn:
        raise SystemExit(f"No agent_arn for '{default}'. Deploy first: agentcore deploy")
    return arn, region or "us-east-1"


def main():
    if len(sys.argv) < 2:
        payload = {"prompt": "What are my costs this month?", "scope": "cost"}
    elif len(sys.argv) == 2 and sys.argv[1].strip().startswith("{"):
        payload = json.loads(sys.argv[1])
    else:
        payload = {"prompt": " ".join(sys.argv[1:])}

    session_id = payload.get("session_id") or payload.get("sessionId") or str(uuid.uuid4())
    payload_bytes = json.dumps(payload).encode("utf-8")
    agent_arn, region = get_agent_arn_and_region()

    timeout_seconds = 300
    client = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(connect_timeout=10, read_timeout=timeout_seconds),
    )
    print("Invoking agent (may take 1–2 min)...", file=sys.stderr, flush=True)
    response = client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        runtimeSessionId=session_id,
        payload=payload_bytes,
        qualifier="DEFAULT",
    )

    content_type = (response.get("contentType") or "").lower()
    stream_body = response.get("response")

    if "text/event-stream" in content_type and stream_body is not None:
        # Streaming: iterate lines, strip "data: " prefix, print each line
        for line in stream_body.iter_lines(chunk_size=1024):
            if line:
                line_str = line.decode("utf-8")
                if line_str.startswith("data: "):
                    line_str = line_str[6:]
                print(line_str, flush=True)
        return

    # Non-streaming (application/json or other): read full body
    parts = []
    if stream_body is not None:
        for chunk in stream_body.iter_chunks():
            if chunk:
                parts.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
    out = "".join(parts)
    try:
        data = json.loads(out)
        print(data.get("result", json.dumps(data)))
    except json.JSONDecodeError:
        print(out)


if __name__ == "__main__":
    main()
