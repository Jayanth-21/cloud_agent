#!/usr/bin/env python3
"""
HTTP server that invokes the AgentCore runtime and streams the response.
Use as the backend for AGENTCORE_RUNTIME_INVOKE_URL so the UI proxy can get SSE.

- POST /invoke with JSON: { "prompt": "...", "sessionId": "...", "scope": "cost"|"logs"|"audit"|"all" }
- If runtime returns text/event-stream: response is streamed as SSE (data: ...\\n\\n).
- If runtime returns application/json: response is buffered and returned as JSON.
- Session: send the same sessionId to keep conversation context (runtimeSessionId).

Run from repo root (where .bedrock_agentcore.yaml lives):
  python agent/streaming_server.py
  # or: python -m agent.streaming_server
Then set AGENTCORE_RUNTIME_INVOKE_URL=http://localhost:8080/invoke (or the host/port you use).
"""
import json
import sys
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

# Add parent so we can load invoke_agent's config helper if desired; we duplicate minimal config here.
import os
import yaml
import boto3
from botocore.config import Config

CONFIG_PATH = ".bedrock_agentcore.yaml"
DEFAULT_PORT = 8080
TIMEOUT_SECONDS = 300


def get_agent_arn_and_region():
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    default = config.get("default_agent")
    if not default or default not in config.get("agents", {}):
        raise ValueError("default_agent not found in .bedrock_agentcore.yaml")
    agent = config["agents"][default]
    arn = agent.get("bedrock_agentcore", {}).get("agent_arn")
    region = agent.get("aws", {}).get("region")
    if not arn:
        raise ValueError(f"No agent_arn for '{default}'. Deploy first: agentcore deploy")
    return arn, region or "us-east-1"


def invoke_and_stream(session_id: str, payload_bytes: bytes, agent_arn: str, region: str):
    """Call invoke_agent_runtime and yield (content_type, chunk_bytes) for streaming, or (content_type, full_body) for non-streaming."""
    client = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(connect_timeout=10, read_timeout=TIMEOUT_SECONDS),
    )
    response = client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        runtimeSessionId=session_id,
        payload=payload_bytes,
        qualifier="DEFAULT",
    )
    content_type = (response.get("contentType") or "").lower()
    stream_body = response.get("response")

    if "text/event-stream" in content_type and stream_body is not None:
        for line in stream_body.iter_lines(chunk_size=1024):
            if line:
                line_str = line.decode("utf-8")
                if not line_str.startswith("data:"):
                    line_str = "data: " + line_str
                # SSE: "data: ...\n\n"
                if not line_str.endswith("\n"):
                    line_str += "\n"
                yield ("text/event-stream", (line_str.strip() + "\n\n").encode("utf-8"))
        return

    parts = []
    if stream_body is not None:
        for chunk in stream_body.iter_chunks():
            if chunk:
                parts.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
    out = "".join(parts)
    yield ("application/json", out.encode("utf-8"))


class InvokeHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/invoke" and not self.path.rstrip("/").endswith("invoke"):
            self.send_error(404, "Not Found")
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length else b"{}"
            data = json.loads(body.decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as e:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Invalid JSON", "details": str(e)}).encode("utf-8"))
            return

        prompt = data.get("prompt", "")
        session_id = data.get("sessionId") or data.get("session_id") or str(uuid.uuid4())
        scope = data.get("scope", "")
        payload = {"prompt": prompt, "scope": scope}
        payload_bytes = json.dumps(payload).encode("utf-8")

        try:
            agent_arn, region = get_agent_arn_and_region()
        except (FileNotFoundError, ValueError) as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Config error", "details": str(e)}).encode("utf-8"))
            return

        try:
            gen = invoke_and_stream(session_id, payload_bytes, agent_arn, region)
            first_ct = None
            first_chunk = None
            for ct, chunk in gen:
                if first_ct is None:
                    first_ct = ct
                    first_chunk = chunk
                    if ct == "text/event-stream" and chunk:
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Cache-Control", "no-cache")
                        self.send_header("Connection", "keep-alive")
                        self.end_headers()
                        self.wfile.write(chunk)
                        self.wfile.flush()
                else:
                    if first_ct == "text/event-stream":
                        self.wfile.write(chunk)
                        self.wfile.flush()

            if first_ct != "text/event-stream":
                body = (first_chunk or b"").decode("utf-8")
                try:
                    parsed = json.loads(body)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(parsed).encode("utf-8"))
                except json.JSONDecodeError:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"result": body}).encode("utf-8"))
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Invoke failed", "details": str(e)}).encode("utf-8"))

    def log_message(self, format, *args):
        print("[%s] %s" % (self.log_date_time_string(), format % args), file=sys.stderr)


def main():
    global CONFIG_PATH
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    host = os.environ.get("HOST", "127.0.0.1")
    # Resolve config: cwd, then agent dir (same as this script), then repo root
    if not os.path.isfile(CONFIG_PATH):
        base = os.path.dirname(os.path.abspath(__file__))
        for candidate in (
            os.path.join(base, ".bedrock_agentcore.yaml"),  # agent/
            os.path.join(base, "..", ".bedrock_agentcore.yaml"),  # repo root
        ):
            if os.path.isfile(candidate):
                CONFIG_PATH = os.path.abspath(candidate)
                break
    server = HTTPServer((host, port), InvokeHandler)
    print("AgentCore streaming server at http://%s:%s/invoke" % (host, port), file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
