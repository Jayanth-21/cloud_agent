# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""MCP client for AgentCore Gateway. All tool calls go through the Gateway (no direct AWS APIs)."""

import logging
import os
from typing import Optional

from langchain_mcp_adapters.client import MultiServerMCPClient

logger = logging.getLogger(__name__)

# Default Gateway MCP URL for this project. Override with GATEWAY_MCP_URL env var if needed.
DEFAULT_GATEWAY_MCP_URL = "https://cloudagent-gateway-tzoaoe5iwu.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"


def _resolve_gateway_url(gateway_url: Optional[str] = None) -> str:
    """Resolve Gateway MCP URL: explicit arg > GATEWAY_MCP_URL env (if non-empty) > default."""
    if gateway_url is not None and (s := (gateway_url or "").strip()):
        url = s
    else:
        env_url = (os.environ.get("GATEWAY_MCP_URL") or "").strip()
        url = env_url if env_url else DEFAULT_GATEWAY_MCP_URL
    if not url.endswith("/mcp"):
        url = url.rstrip("/") + "/mcp"
    return url


def get_streamable_http_mcp_client(
    gateway_url: Optional[str] = None,
    access_token: Optional[str] = None,
) -> MultiServerMCPClient:
    """
    Returns an MCP client pointing at the AgentCore Gateway MCP endpoint.
    All tools/list and tools/call go through the Gateway to Lambda-hosted MCP servers.
    Uses DEFAULT_GATEWAY_MCP_URL when GATEWAY_MCP_URL is unset or empty.
    """
    url = _resolve_gateway_url(gateway_url)
    logger.info("gateway url=%s", url)
    headers = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return MultiServerMCPClient(
        {
            "gateway": {
                "transport": "streamable_http",
                "url": url,
                **({"headers": headers} if headers else {}),
            }
        }
    )
