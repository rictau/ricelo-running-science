# Ricelo Running Science

[![PyPI](https://img.shields.io/pypi/v/ricelo-running-science.svg)](https://pypi.org/project/ricelo-running-science/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Model Context Protocol](https://img.shields.io/badge/MCP-Standard-purple.svg)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/rictau/ricelo-running-science/blob/master/LICENSE)

Running analytics for your Garmin data, as an MCP server for AI assistants and as a command-line tool. It reads your activities through Garmin Connect's **Personal API** and computes laps and cardiac drift, training load (CTL / ATL / TSB), injury risk (ACWR), VDOT training paces, personal records and daily readiness. The formulas are documented in [docs/science.md](https://github.com/rictau/ricelo-running-science/blob/master/docs/science.md).

## Quick Start

1. Create a Personal API key in [Garmin Connect settings](https://connect.garmin.com/app/settings/personalApiToken).
2. Save it. The key gives access to your Garmin data, so keep it private: type it into this prompt yourself, and don't share it with anyone or paste it into an AI chat. (Input is hidden; the file is readable only by you.)
   ```bash
   printf "Garmin Personal API key: " && read -s val && echo && mkdir -p ~/.config/garmin && echo "$val" > ~/.config/garmin/connect-personal-api-key && chmod 600 ~/.config/garmin/connect-personal-api-key
   ```
   Or set the `GARMIN_PERSONAL_API_KEY` environment variable, which takes precedence.
3. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if you don't have it, then check the connection (this downloads ricelo from PyPI; nothing to install first):
   ```bash
   uvx --from ricelo-running-science ricelo auth
   ```

On **Windows**, save the key in PowerShell instead (the input is visible):
```powershell
New-Item -ItemType Directory -Force "$HOME\.config\garmin" | Out-Null; Read-Host "Garmin Personal API key" | Set-Content -NoNewline -Encoding ascii "$HOME\.config\garmin\connect-personal-api-key"
```

## Add to Your AI Client

Add this entry to your client's MCP config, with `/path/to/uvx` replaced by the output of `which uvx` (desktop apps don't see your terminal's `PATH`). If the file already has `"mcpServers"`, add just the `"ricelo"` entry inside it.

```json
{
  "mcpServers": {
    "ricelo": {
      "command": "/path/to/uvx",
      "args": ["ricelo-running-science@latest"]
    }
  }
}
```

| Client | Where it goes |
| :--- | :--- |
| Claude Desktop | Settings → Developer → **Edit Config**, then quit (Cmd+Q) and reopen |
| Cursor | `~/.cursor/mcp.json` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` |
| Antigravity | `~/.gemini/config/mcp_config.json` |
| VS Code | **MCP: Open User Configuration**; use `"servers"` instead of `"mcpServers"` and add `"type": "stdio"` to the entry |
| Claude Code | `claude mcp add ricelo -- uvx ricelo-running-science@latest` |
| Codex | `codex mcp add ricelo -- uvx ricelo-running-science@latest` |

On Windows, get the path with `where.exe uvx` and write it with forward slashes, e.g. `"C:/Users/you/.local/bin/uvx.exe"`; `~` in the paths above is your user folder, and Claude Desktop is quit from its system-tray icon.

To pass the key in the config instead of the key file, add `"env": { "GARMIN_PERSONAL_API_KEY": "..." }`. ChatGPT, claude.ai on the web and mobile apps only support remote MCP servers, so they can't run ricelo.

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
uv tool install ricelo-running-science
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

- **MCP server:** with `@latest`, `uvx` checks PyPI each time the server starts and fetches a new release, so quitting and reopening your AI client is enough.
- **CLI installed with `uv tool install`:**
  ```bash
  uv tool upgrade ricelo-running-science
  ```

## Development

```bash
git clone https://github.com/rictau/ricelo-running-science.git && cd ricelo-running-science
uv run ricelo auth    # run the CLI from your local code
uv run pytest         # run the tests
```

To use your local code as the MCP server, set `"command": "/path/to/uv"` and `"args": ["run", "--directory", "/path/to/ricelo-running-science", "ricelo-running-science-mcp"]`. Inside the repo, Claude Code and Cursor load it automatically via `.mcp.json` and `.cursor/mcp.json`.

To release: bump `version` in `pyproject.toml`, merge, then publish a GitHub release tagged `v<version>` (e.g. `v0.4.1`). The publish workflow tests, builds and uploads it to PyPI.

## License

MIT. See [LICENSE](https://github.com/rictau/ricelo-running-science/blob/master/LICENSE).
