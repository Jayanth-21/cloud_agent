# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Local HTTP + SSE server for the Cloud Intelligence agent (laptop dev).
Same event shape as Bedrock AgentCore HTTP streaming so ui/ AGENTCORE_RUNTIME_INVOKE_URL works.

POST /invoke  JSON: { prompt, session_id|sessionId, scope? }
GET  /health
"""

import json
import logging
from collections.abc import AsyncIterator

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from runtime_invoke import stream_agent_events

logger = logging.getLogger(__name__)


def _sse_bytes(chunk: dict) -> bytes:
    line = f"data: {json.dumps(chunk, default=str)}\n\n"
    return line.encode("utf-8")


async def _event_stream(payload: dict) -> AsyncIterator[bytes]:
    try:
        async for event in stream_agent_events(payload):
            yield _sse_bytes(event)
    except Exception as e:
        logger.exception("stream failed: %s", e)
        err = {"stage": "error", "message": str(e), "result": f"Agent error: {e}"}
        yield _sse_bytes(err)


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "cloud-intelligence-local-agent"})


async def invoke(request: Request) -> StreamingResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "Body must be a JSON object"}, status_code=400)

    return StreamingResponse(
        _event_stream(payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


routes = [
    Route("/health", health, methods=["GET"]),
    Route("/invoke", invoke, methods=["POST"]),
]

app = Starlette(routes=routes)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
