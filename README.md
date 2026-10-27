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
3. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if you don't have it, then check the connection (this downloads ricelo from GitHub; no clone needed):
   ```bash
   uvx --from git+https://github.com/rictau/ricelo-running-science.git ricelo auth
   ```

## Add to Your AI Client

Add this server entry to your client's config. Replace `/path/to/uvx` with the output of `which uvx`: desktop apps don't see your terminal's `PATH`, so a bare `uvx` can fail with "command not found".

```json
{
  "mcpServers": {
    "ricelo": {
      "command": "/path/to/uvx",
      "args": ["--from", "git+https://github.com/rictau/ricelo-running-science.git", "ricelo-running-science-mcp"]
    }
  }
}
```

If the config file already has an `"mcpServers"` section, add only the `"ricelo": { … }` entry inside it, separated from the others by a comma.

| Client | Where it goes |
| :--- | :--- |
| Claude Desktop | Settings → Developer → **Edit Config** opens `claude_desktop_config.json`. Save, then quit with Cmd+Q and reopen; **ricelo** should show as running under Settings → Developer. |
| Cursor | `~/.cursor/mcp.json` |
| Antigravity | `~/.gemini/config/mcp_config.json` |
| Claude Code | `claude mcp add ricelo -- uvx --from git+https://github.com/rictau/ricelo-running-science.git ricelo-running-science-mcp` |
| Codex | `codex mcp add ricelo -- uvx --from git+https://github.com/rictau/ricelo-running-science.git ricelo-running-science-mcp` |

The server reads your key from the file saved in Quick Start. To pass it from the config instead, add `"env": { "GARMIN_PERSONAL_API_KEY": "..." }` to the entry.

**From a clone (for development):** use `"command": "/path/to/uv"` with `"args": ["run", "--directory", "/path/to/ricelo-running-science", "ricelo-running-science-mcp"]` to run your local code. Inside the repo, Claude Code loads it automatically via `.mcp.json` and Cursor via `.cursor/mcp.json`.

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

Readiness needs the watch to be worn overnight; without sleep or HRV data it gives no verdict. Resting HR and stress show N/A, because the daily summary they come from isn't available through the Personal API.

## Command Line

Install the commands once, then run `ricelo <command>` (aliases: `ricelo-cli`, `garmin-cli`):

```bash
uv tool install git+https://github.com/rictau/ricelo-running-science.git
```

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

## Updating

- **MCP server:** `uvx` checks GitHub each time the server starts and fetches the latest version, so quitting and reopening your AI client is enough.
- **CLI installed with `uv tool install`:** reinstall to update:
  ```bash
  uv tool install --force git+https://github.com/rictau/ricelo-running-science.git
  ```

## Development

```bash
git clone https://github.com/rictau/ricelo-running-science.git && cd ricelo-running-science
uv run ricelo auth    # run the CLI from your local code
uv run pytest         # run the tests
```

## License

MIT. See [LICENSE](LICENSE).
