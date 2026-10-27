"""Tests for the MCP server's registered tool surface."""

import asyncio

from ricelo.mcp_server import mcp

EXPECTED_TOOLS = {
    "analyze_run",
    "compare_runs",
    "get_acwr_injury_risk",
    "get_fitness_and_freshness",
    "get_personal_records",
    "get_readiness_assessment",
    "get_training_volume",
    "get_vdot_paces",
}


def test_registered_tools_match_expected():
    names = [tool.name for tool in asyncio.run(mcp.list_tools())]
    assert len(names) == len(set(names)), "duplicate tool registrations"
    assert set(names) == EXPECTED_TOOLS
