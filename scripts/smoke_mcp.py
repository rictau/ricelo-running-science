"""Smoke test: start the MCP server the way an AI client does and check its tools.

Usage: python scripts/smoke_mcp.py <command> [args...]
e.g.   python scripts/smoke_mcp.py uvx --from dist/ricelo_running_science-0.4.0-py3-none-any.whl ricelo-running-science

Talks to the server over stdio with the MCP client library and exits non-zero
if it fails to start or does not list the expected tools. It makes no Garmin
calls, so it needs no API key.
"""

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "analyze_run",
    "compare_runs",
    "get_acwr_injury_risk",
    "get_fitness_and_freshness",
    "get_personal_records",
    "get_readiness_assessment",
    "get_training_volume",
    "get_vdot_paces",
    "list_activities",
}


async def main(command: str, args: list[str]) -> None:
    params = StdioServerParameters(command=command, args=args, env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
    print(f"server {info.serverInfo.name} listed {len(names)} tools")
    if names != EXPECTED_TOOLS:
        sys.exit(f"tool mismatch: missing {sorted(EXPECTED_TOOLS - names)}, unexpected {sorted(names - EXPECTED_TOOLS)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(asyncio.wait_for(main(sys.argv[1], sys.argv[2:]), timeout=300))
