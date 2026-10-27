"""Garmin Connect Personal API Performance & Activity Analytics Toolkit.

Provides advanced activity analysis, historical performance tracking,
fitness/fatigue modeling, recovery correlation, and MCP tools for Garmin Connect.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ricelo-running-science")
except PackageNotFoundError:
    __version__ = "0.0.0"
