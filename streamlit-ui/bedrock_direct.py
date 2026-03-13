"""
Call Bedrock invoke_agent_runtime directly (same as invoke_agent.py). No HTTP, no timeout.
Use when the streaming server causes Read timed out. Set USE_DIRECT_BEDROCK=1 and run
Streamlit from repo root so config is found: cd streamlit-ui && set USE_DIRECT_BEDROCK=1 && streamlit run app.py
"""
import json
import logging
import os

logger = logging.getLogger(__name__)
from typing import Iterator

# Optional deps for direct Bedrock path
try:
    import boto3
    import yaml
    from botocore.config import Config
except ImportError:
    boto3 = None
    yaml = None
    Config = None


def _find_config() -> str | None:
    base = os.path.dirname(os.path.abspath(__file__))
    for path in (
        os.environ.get("AGENT_CONFIG_PATH"),
        os.path.join(base, "..", ".bedrock_agentcore.yaml"),
        os.path.join(base, "..", "agent", ".bedrock_agentcore.yaml"),
        os.path.join(os.getcwd(), ".bedrock_agentcore.yaml"),
        os.path.join(os.getcwd(), "agent", ".bedrock_agentcore.yaml"),
    ):
        if path and os.path.isfile(path):
            return path
    return None


def invoke_stream_direct(
    prompt: str,
    session_id: str,
    scope: str = "",
) -> Iterator[str]:
    """Call Bedrock invoke_agent_runtime and yield progress messages + result. No HTTP."""
    if not boto3 or not yaml:
        yield "Direct Bedrock requires boto3 and pyyaml. Install: pip install boto3 pyyaml"
        return

    config_path = _find_config()
    if not config_path:
        yield "Config not found. Run from repo root or set AGENT_CONFIG_PATH to .bedrock_agentcore.yaml"
        return

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
    except Exception as e:
        yield f"Config error: {e}"
        return

    default = config.get("default_agent")
    if not default or default not in config.get("agents", {}):
        yield "default_agent not found in .bedrock_agentcore.yaml"
        return
    agent = config["agents"][default]
    arn = agent.get("bedrock_agentcore", {}).get("agent_arn")
    region = agent.get("aws", {}).get("region") or "us-east-1"
    if not arn:
        yield "No agent_arn. Run: agentcore deploy"
        return

    # Include session_id so the agent can load/save conversation history (session memory)
    payload = {
        "prompt": prompt,
        "scope": scope or "",
        "session_id": session_id,
    }
    logger.info("invoke_stream_direct session_id=%s prompt_len=%d", session_id, len(prompt or ""))
    payload_bytes = json.dumps(payload).encode("utf-8")

    client = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(connect_timeout=10, read_timeout=600),
    )
    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=session_id,
        payload=payload_bytes,
        qualifier="DEFAULT",
    )
    content_type = (response.get("contentType") or "").lower()
    stream_body = response.get("response")

    if "text/event-stream" in content_type and stream_body:
        for line in stream_body.iter_lines(chunk_size=1024):
            if not line:
                continue
            line_str = line.decode("utf-8")
            if line_str.startswith("data:"):
                line_str = line_str[5:].strip()
            if not line_str:
                continue
            try:
                obj = json.loads(line_str)
                if isinstance(obj, dict):
                    if obj.get("stage") == "progress":
                        continue
                    chunk = (
                        obj.get("result")
                        or obj.get("content")
                        or obj.get("text")
                        or obj.get("delta")
                        or obj.get("output")
                        or obj.get("answer")
                        or ""
                    )
                    if isinstance(chunk, str) and chunk:
                        yield chunk
                    if obj.get("clarification_needed"):
                        yield {"clarification_needed": True}
            except json.JSONDecodeError:
                yield line_str
        return

    parts = []
    if stream_body:
        for chunk in stream_body.iter_chunks():
            if chunk:
                parts.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
    out = "".join(parts)
    try:
        data = json.loads(out)
        result = data.get("result") or data.get("content") or data.get("text") or out
        yield result if isinstance(result, str) else json.dumps(data)
    except json.JSONDecodeError:
        yield out
