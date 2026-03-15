"""
Call AgentCore Runtime directly (boto3 invoke_agent_runtime). No streaming server.
Run Streamlit from repo root so .bedrock_agentcore.yaml is found, with AWS credentials configured.
"""
from typing import Iterator

from bedrock_direct import invoke_stream_direct


def invoke_stream(
    prompt: str,
    session_id: str,
    scope: str = "",
) -> Iterator[str]:
    """
    Yield progress and final result by calling the deployed agent runtime directly.
    Same session_id keeps conversation context (in-memory on the runtime).
    """
    yield from invoke_stream_direct(prompt, session_id, scope)
