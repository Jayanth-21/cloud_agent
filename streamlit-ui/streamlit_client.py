"""
Single API call: either direct Bedrock (no HTTP) or POST to streaming server.
Use direct Bedrock to avoid Read timed out: set USE_DIRECT_BEDROCK=1 and run from repo root.
"""
import json
import os
from typing import Iterator

import requests

DEFAULT_URL = os.environ.get("STREAMING_SERVER_URL", "http://127.0.0.1:8080/invoke")


def invoke_stream(
    prompt: str,
    session_id: str,
    scope: str = "",
    url: str | None = None,
) -> Iterator[str]:
    """
    Yield progress messages and final result. Uses direct Bedrock if USE_DIRECT_BEDROCK=1
    (same path as invoke_agent.py; no HTTP timeout). Otherwise POST to streaming server.
    """
    if os.environ.get("USE_DIRECT_BEDROCK", "").strip().lower() in ("1", "true", "yes"):
        from bedrock_direct import invoke_stream_direct
        yield from invoke_stream_direct(prompt, session_id, scope)
        return

    base_url = url or DEFAULT_URL
    payload = {"prompt": prompt, "sessionId": session_id}
    if scope:
        payload["scope"] = scope

    # Long read timeout: agent can take minutes before sending first byte (cost/logs queries).
    resp = requests.post(
        base_url,
        json=payload,
        headers={"Accept": "text/event-stream"},
        stream=True,
        timeout=(15, 600),  # 15s connect, 10 min read
    )
    resp.raise_for_status()

    content_type = (resp.headers.get("Content-Type") or "").lower()
    if "text/event-stream" in content_type:
        yielded_any = False
        got_result = False
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("data:"):
                data = line[5:].strip()
                if data == "[DONE]" or not data:
                    continue
                try:
                    obj = json.loads(data)
                    if isinstance(obj, dict):
                        # Progress events: show named message so user sees what’s happening
                        if obj.get("stage") == "progress":
                            continue
                        # Final/content shapes: result, content, text, delta, output, answer
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
                            yielded_any = True
                            got_result = True
                    elif isinstance(obj, str):
                        yield obj
                        yielded_any = True
                        got_result = True
                except json.JSONDecodeError:
                    yield data
                    yielded_any = True
        if not yielded_any:
            yield "No response content from agent."
        elif not got_result:
            yield "\n\nResponse incomplete or timed out before the final answer could be sent."
    else:
        text = resp.text
        try:
            parsed = json.loads(text)
            result = parsed.get("result") or parsed.get("content") or parsed.get("text") or text
            yield result if isinstance(result, str) else json.dumps(result)
        except json.JSONDecodeError:
            yield text
