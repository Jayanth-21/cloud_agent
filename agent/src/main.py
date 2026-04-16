# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Cloud Intelligence agent entry.

- Default (local laptop): run ``python src/main.py`` → Starlette + uvicorn on PORT (8080).
- AgentCore deploy: set CLOUD_AGENT_AGENTCORE=1 and use BedrockAgentCoreApp (same entry file).
"""

import logging
import os
import sys

from bedrock_agentcore import BedrockAgentCoreApp

from runtime_invoke import stream_agent_events

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()


@app.entrypoint
async def invoke(payload: dict):
    """AgentCore HTTP runtime: stream same dicts as local server."""
    async for event in stream_agent_events(payload):
        yield event


def _run_local_http() -> None:
    import uvicorn

    from local_http import app as http_app

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    log_level = os.environ.get("UVICORN_LOG_LEVEL", "info")
    logger.info("Starting local agent HTTP on http://%s:%s (POST /invoke)", host, port)
    print(
        f"[AGENT] Local HTTP  http://{host}:{port}/invoke  "
        f"Set ui AGENTCORE_RUNTIME_INVOKE_URL to this URL",
        flush=True,
    )
    uvicorn.run(http_app, host=host, port=port, log_level=log_level)


if __name__ == "__main__":
    in_container = os.environ.get("DOCKER_CONTAINER", "").lower() in ("1", "true", "yes")
    force_agentcore = os.environ.get("CLOUD_AGENT_AGENTCORE", "").lower() in (
        "1",
        "true",
        "yes",
    )
    # AgentCore Dockerfiles set DOCKER_CONTAINER=1 and run ``python -m src.main`` → Bedrock SDK server.
    if in_container or force_agentcore:
        app.run()
    else:
        src_dir = os.path.dirname(os.path.abspath(__file__))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        _run_local_http()
