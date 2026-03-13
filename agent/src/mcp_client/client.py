# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""MCP client for AgentCore Gateway. All tool calls go through the Gateway (no direct AWS APIs)."""

import os
from typing import Optional

from langchain_mcp_adapters.client import MultiServerMCPClient

# Default Gateway MCP URL (used when env GATEWAY_MCP_URL is not set, e.g. after runtime env reset).
DEFAULT_GATEWAY_MCP_URL = "https://cloudagent-gateway-tzoaoe5iwu.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"


def get_streamable_http_mcp_client(
    gateway_url: Optional[str] = None,
    access_token: Optional[str] = None,
) -> MultiServerMCPClient:
    """
    Returns an MCP client pointing at the AgentCore Gateway MCP endpoint.
    All tools/list and tools/call go through the Gateway to Lambda-hosted MCP servers.
    """
    url = gateway_url or os.environ.get("GATEWAY_MCP_URL", "") or DEFAULT_GATEWAY_MCP_URL
    if not url:
        raise ValueError(
            "GATEWAY_MCP_URL must be set (or pass gateway_url) to use the agent. "
            "Set it to your AgentCore Gateway MCP endpoint (e.g. https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp)."
        )
    if not url.endswith("/mcp"):
        url = url.rstrip("/") + "/mcp"
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
