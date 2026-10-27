# Ricelo Running Science

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Model Context Protocol](https://img.shields.io/badge/MCP-Standard-purple.svg)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Running analytics for your Garmin data, as an MCP server for AI assistants and as a command-line tool. It reads your activities through Garmin Connect's **Personal API** and computes laps and cardiac drift, training load (CTL / ATL / TSB), injury risk (ACWR), VDOT training paces, personal records and daily readiness. The formulas are documented in [docs/science.md](docs/science.md).

## Quick Start

1. Create a Personal API key in [Garmin Connect settings](https://connect.garmin.com/app/settings/personalApiToken).
2. Save it (input is hidden; the file is readable only by you):
   ```bash
   printf "Garmin Personal API key: " && read -s val && echo && mkdir -p ~/.config/garmin && echo "$val" > ~/.config/garmin/connect-personal-api-key && chmod 600 ~/.config/garmin/connect-personal-api-key
   ```
   Or set the `GARMIN_PERSONAL_API_KEY` environment variable, which takes precedence.
3. Check the connection:
   ```bash
   uv run ricelo auth
   ```

## Add to Your AI Client

Most clients take the same server entry. Replace `/path/to/ricelo-running-science` with your clone, and `/path/to/uv` with the output of `which uv` (desktop apps often don't see your shell's `PATH`):

```json
{
  "mcpServers": {
    "ricelo-running-science": {
      "command": "/path/to/uv",
      "args": ["run", "--directory", "/path/to/ricelo-running-science", "ricelo-running-science-mcp"]
    }
  }
}
```

| Client | Where it goes |
| :--- | :--- |
| Claude Desktop | Settings → Developer → **Edit Config**, then restart the app |
| Cursor | `~/.cursor/mcp.json` (this repo's `.cursor/mcp.json` also loads it when the project is open) |
| Antigravity | `~/.gemini/config/mcp_config.json` |
| Claude Code | Automatic in this repo via `.mcp.json` (uses `GARMIN_PERSONAL_API_KEY`), or `claude mcp add ricelo-running-science -- uv run --directory /path/to/ricelo-running-science ricelo-running-science-mcp` |
| Codex | `codex mcp add ricelo-running-science -- uv run --directory /path/to/ricelo-running-science ricelo-running-science-mcp` |

To run it without cloning, use `"command": "uvx"` with `"args": ["--from", "git+https://github.com/rictau/ricelo-running-science.git", "ricelo-running-science-mcp"]`. To pass the key from the config instead of the key file, add `"env": { "GARMIN_PERSONAL_API_KEY": "..." }`.

## MCP Tools

| Tool | Returns |
| :--- | :--- |
| `list_activities` | Recent activities with their Garmin IDs (`limit`, `activity_type`) |
| `analyze_run` | Pace, HR, cadence, elevation, Training Effect, the watch's laps and cardiac drift for one activity |
| `compare_runs` | Side-by-side deltas for two activities: pace, HR, efficiency, elevation, drift |
| `get_training_volume` | Distance, time, elevation and calories by week or month |
| `get_fitness_and_freshness` | Daily fitness (CTL), fatigue (ATL) and form (TSB) |
| `get_acwr_injury_risk` | Acute-to-chronic workload ratio and risk zone |
| `get_vdot_paces` | VDOT, training paces and race predictions from your best effort of the last year |
| `get_personal_records` | Fastest 5K/10K/half/marathon (measured from FIT files), longest run, most elevation |
| `get_readiness_assessment` | Readiness from last night's sleep, HRV and Body Battery |

Readiness needs the watch to be worn overnight; without sleep or HRV data it gives no verdict. Resting HR and stress are not available through the Personal API.

## Command Line

`uv run ricelo <command>` (aliases: `ricelo-cli`, `garmin-cli`):

| Command | What it shows |
| :--- | :--- |
| `auth` | Connection check |
| `activities -n 10 -t running` | Recent activities |
| `activity <id>` | One activity: laps, cadence, elevation, drift |
| `compare <id> <id>` | Two activities side by side |
| `volume -w 8` | Weekly or monthly volume |
| `fitness -d 30` | CTL / ATL / TSB timeline |
| `acwr` | Workload ratio and injury risk |
| `vdot` | VDOT, paces and race predictions (`-d 5000 -t 24:41` for a manual effort) |
| `best-efforts -n 2000` | Personal records over the last `-n` activities |
| `readiness` | Today's readiness |
| `vo2max -n 8` | VO2 max to two decimals, from FIT files |
| `download <id>` | Download and summarise an activity's FIT file |
| `dashboard -w 12` | Standalone HTML dashboard (`--max-hr` to override Garmin's max HR) |

## Development

```bash
uv run pytest
```

## License

MIT. See [LICENSE](LICENSE).
