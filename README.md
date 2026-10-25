# Ricelo Running Science

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Model Context Protocol](https://img.shields.io/badge/MCP-Standard-purple.svg)](https://modelcontextprotocol.io)
[![Built with FastMCP](https://img.shields.io/badge/Built%20with-FastMCP-10B981.svg)](https://github.com/modelcontextprotocol/python-sdk)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Precision Running Telemetry, Sub-Integer VO2 Max Progression, and Training Load Intelligence for AI & Humans**

A 100% self-contained, native running performance analytics engine and Model Context Protocol (MCP) server powered by Garmin Connect's official **Personal API**.

Built to deliver high-resolution running mechanics, endurance training load curves (Banister CTL / ATL / TSB), lap-by-lap cardiac drift (aerobic decoupling), sub-integer VO2 max progression extracted from raw binary `.FIT` sensor streams, and multi-biometric recovery readiness.

---

## Table of Contents

- [What is Inside This MCP](#what-is-inside-this-mcp)
  - [Detailed Tool Reference (8 Tools)](#detailed-tool-reference-the-8-mcp-tools)
- [Quick Start](#quick-start-authenticating-your-key)
- [Client Installation Guide](#client-installation-guide)
  - [A. Codex CLI / Codex App](#a-codex-cli--codex-app)
  - [B. Cursor](#b-cursor)
  - [C. Claude Desktop & Claude Code](#c-claude-desktop)
  - [D. Antigravity IDE](#d-antigravity-ide)
  - [E. Zero-Clone Remote Distribution via uvx](#e-zero-clone-remote-distribution-via-uvx)
- [CLI Features](#cli-features-ricelo--ricelo-cli)
- [Sports Science Reference](#sports-science-reference)
  - [1. Efficiency: Efficiency Factor (EF)](#1-efficiency-efficiency-factor-ef-performancepy)
  - [2. Aerobic Efficiency: Cardiac Drift & Decoupling](#2-aerobic-efficiency-cardiac-drift--decoupling-performancepy)
  - [3. VO₂ Max: Sub-Integer Telemetry](#3-vo-max-sub-integer-telemetry-fitpy)
  - [4. Fitness & Fatigue: CTL / ATL / TSB](#4-fitness--fatigue-ctl--atl--tsb-performancepy)
  - [5. Injury Risk Prevention: ACWR](#5-injury-risk-prevention-acwr-performancepy)
  - [6. Pacing & Race Dynamics: Jack Daniels VDOT](#6-pacing--race-dynamics-jack-daniels-vdot-performancepy)
- [Testing](#testing)

---

## What is Inside This MCP

Unlike generic wrappers that dump unparsed Garmin JSON into an AI model's context window, **`ricelo-running-science`** is a purpose-built **running sports science intelligence layer**. It computes actionable physiological metrics, endurance models, and split comparisons before responding to your prompts.

```
┌────────────────────────────────────────────────────────────────────────┐
│             Your AI Client (Cursor / Claude / Codex / Antigravity)     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Calls MCP Tools
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│             Ricelo Running Science MCP (FastMCP Server)                │
│                                                                        │
│  1. analyze_run                    → Pace, Splits, Cardiac Drift %     │
│  2. compare_runs                   → Head-to-head deltas & Efficiency  │
│  3. get_training_volume            → Weekly or monthly running mileage │
│  4. get_fitness_and_freshness      → Daily CTL Fitness / ATL Fatigue   │
│  5. get_personal_records           → Career Running PRs (42K, 21K, etc)│
│  6. get_readiness_assessment       → Sleep + HRV + Body Battery + RHR  │
│  7. get_acwr_injury_risk           → 7d acute vs 28d chronic sweet spot│
│  8. get_vdot_paces                 → Daniels VDOT & prescribed paces   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Invokes
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     Local Analytics Engine                             │
│                                                                        │
│  • client.py      Native token exchange and auto-refresh (Zero 3rd)    │
│  • performance.py Banister model, ACWR, VDOT, Decoupling, Career PRs   │
│  • recovery.py    Multi-biometric readiness synthesis                  │
│  • fit.py         Binary .FIT parser (Message 140 Sub-integer VO2 max) │
│  • reporting.py   Self-contained interactive visual dashboard          │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTPS (Bearer Token)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│         Garmin Connect Personal API (personal.connectapi.garmin.com)   │
└────────────────────────────────────────────────────────────────────────┘
```

---

### Detailed Tool Reference (The 8 MCP Tools)

*(Clean standard tool names; legacy `ricelo_*` and `garmin_*` aliases are also supported)*

| Tool Name | Parameters | What It Computes & Returns |
| :--- | :--- | :--- |
| **`analyze_run`** | `activity_id: int` | Converts speed to pace (`MM:SS /km`), formats elevation and cadence, and calculates **Aerobic Decoupling (Cardiac Drift %)** between the first half and second half of the run. Returns lap splits and an endurance stability diagnosis (`<5% stable` vs `>8% fatigue`). |
| **`compare_runs`** | `activity_id_a: int`, `activity_id_b: int` | Automates side-by-side run comparisons: computes exact **Pace Deltas** (s/km), **Heart Rate Deltas** (bpm), **Efficiency Factor Deltas** (Speed/HR ratio), and elevation differentials with verbal verdicts. |
| **`get_training_volume`** | `weeks: int = 12`, `frequency: str = "W"` | Periodizes training history: aggregates distance (km), duration (hours), elevation climb (m), and calories burned by week or month, broken down across running sessions. |
| **`get_fitness_and_freshness`** | `days: int = 60` | Models long-term training stress balance using standard endurance mathematics: **CTL (Chronic Training Load / Fitness: 42-day rolling base)**, **ATL (Acute Training Load / Fatigue: 7-day stress)**, and **TSB (Form / Freshness)**. |
| **`get_personal_records`** | *None* | Scans your entire historical running career back to 2023 to discover authentic milestone records: Marathon (42.2K), Fastest Half Marathon, Fastest 10K, Fastest 5K, Longest Run, and Max Elevation Gain. |
| **`get_readiness_assessment`** | *None* | Synthesizes your biometrics: Sleep score, total sleep, deep and REM durations, overnight HRV average vs weekly baseline, Body Battery highest / charged, and resting HR into an actionable daily training recommendation. |
| **`get_acwr_injury_risk`** | *None* | Computes **Acute-to-Chronic Workload Ratio (Gabbett 2016)** comparing 7-day acute fatigue against 28-day baseline conditioning to diagnose injury risk (`Sweet Spot 0.80 - 1.30` vs `Danger Zone > 1.50`). |
| **`get_vdot_paces`** | `distance_m: float?`, `duration_s: float?` | Calculates **Jack Daniels VDOT**, equivalent race finish predictions (5K, 10K, Half, Full), and exact training target paces (Easy, Marathon, Threshold, Interval, Repetition) based on your peak empirical benchmark. |

---

## Quick Start: Authenticating Your Key

The Garmin Personal API uses a revocable Personal API Key generated in your [Garmin Connect Web Settings](https://connect.garmin.com/app/settings/personalApiToken).

### Step 1: Securely Save Your Key
Run this in your terminal to save your key to `~/.config/garmin/connect-personal-api-key` with owner-only (`0600`) permissions (input is hidden):

```bash
printf "Enter GARMIN_PERSONAL_API_KEY (typing hidden): " && read -s val && echo && mkdir -p ~/.config/garmin && echo "$val" > ~/.config/garmin/connect-personal-api-key && chmod 600 ~/.config/garmin/connect-personal-api-key && echo "Saved to ~/.config/garmin/connect-personal-api-key"
```

### Step 2: Verify Connection
```bash
uv run ricelo auth
```

---

## Client Installation Guide

### A. Codex CLI / Codex App
Add the server globally using the Codex CLI:
```bash
codex mcp add ricelo-running-science -- /Users/ricki/.local/bin/uv run --directory /Users/ricki/Documents/github/exploration/garmin ricelo-running-science-mcp
```
To verify in Codex:
```bash
codex mcp list
```

### B. Cursor
Cursor supports both global and project-level configs:
* **Global (`~/.cursor/mcp.json`):**
  ```json
  {
    "mcpServers": {
      "ricelo-running-science": {
        "command": "/Users/ricki/.local/bin/uv",
        "args": [
          "run",
          "--directory",
          "/Users/ricki/Documents/github/exploration/garmin",
          "ricelo-running-science-mcp"
        ]
      }
    }
  }
  ```
* **Project Level (`.cursor/mcp.json`):** Committed directly in this repo. Whenever you open this directory in Cursor, the MCP server is bound automatically.

### C. Claude Desktop
Add to your `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "ricelo-running-science": {
      "command": "/Users/ricki/.local/bin/uv",
      "args": [
        "run",
        "--directory",
        "/Users/ricki/Documents/github/exploration/garmin",
        "ricelo-running-science-mcp"
      ]
    }
  }
}
```

### D. Antigravity IDE
Configured in `~/.gemini/config/mcp_config.json`:
```json
{
  "mcpServers": {
    "ricelo-running-science": {
      "command": "/Users/ricki/.local/bin/uv",
      "args": [
        "run",
        "--directory",
        "/Users/ricki/Documents/github/exploration/garmin",
        "ricelo-running-science-mcp"
      ]
    }
  }
}
```

### E. Zero-Clone Remote Distribution via `uvx`
Any user on another machine can run this MCP directly from GitHub without cloning:
```json
{
  "mcpServers": {
    "ricelo-running-science": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/rictau/ricelo-running-science.git",
        "ricelo-running-science-mcp"
      ]
    }
  }
}
```

---

## CLI Features (`ricelo` / `ricelo-cli`)

For quick queries in your terminal without using an AI model:

| Command | Description | Example |
| :--- | :--- | :--- |
| `activities` | List recent activities with pace, distance, HR, and Aerobic TE | `uv run ricelo activities -n 10 -t running` |
| `activity` | Deep dive into an activity: lap splits, cadence, elevation, and drift | `uv run ricelo activity 24841273382` |
| `vo2max` | Extract exact sub-integer VO2 max from Firstbeat FIT sensor streams | `uv run ricelo vo2max -n 8` |
| `volume` | Weekly or monthly volume breakdown (distance, hours, elevation, sports) | `uv run ricelo volume -w 8 -f W` |
| `fitness` | Fitness (CTL), Fatigue (ATL), and Form (TSB) training load progression | `uv run ricelo fitness -d 30` |
| `compare` | Side-by-side comparison of two runs (pace, HR efficiency, elevation, drift)| `uv run ricelo compare 24762216656 24841273382` |
| `best-efforts` | Historical landmark running efforts (Marathon, Half, 10K, 5K, Max Vert) | `uv run ricelo best-efforts` |
| `readiness` | Daily recovery synthesis: Sleep score + Overnight HRV + Body Battery + RHR | `uv run ricelo readiness` |
| `download` | Download and parse original binary `.FIT` file telemetry streams | `uv run ricelo download 24841273382` |
| `acwr` | Acute-to-Chronic Workload Ratio & soft-tissue injury risk gauge | `uv run ricelo acwr` |
| `vdot` | Jack Daniels VDOT, equivalent race predictions, and target paces | `uv run ricelo vdot` |
| `dashboard` | Generate interactive standalone HTML dashboard with Chart.js | `uv run ricelo dashboard -w 12` |

*(Note: `garmin-cli` is also preserved as a command alias).*

---

## Sports Science Reference

`ricelo-running-science` rejects black-box proprietary metrics in favor of peer-reviewed exercise physiology and open endurance mathematics.

---

### 1. Efficiency: Efficiency Factor (EF) (`performance.py`)
Popularized by endurance physiologist Joe Friel, **Efficiency Factor (EF)** quantifies mechanical output produced per cardiovascular beat (speed-to-heart-rate ratio):

$$\text{EF} = \frac{\text{Velocity (m/s)}}{\text{Heart Rate (bpm)}} \times 1000$$

A rising EF across weeks at similar training paces indicates improved running economy and aerobic engine development—enabling you to run faster at the same heart rate or maintain pace with less cardiac strain.

---

### 2. Aerobic Efficiency: Cardiac Drift & Decoupling (`performance.py`)
During steady aerobic running, heart rate often drifts upward despite constant pace due to cutaneous vasodilation, dehydration, and motor unit fatigue. Aerobic Decoupling evaluates cardiovascular stability by comparing the first half ($\text{EF}_1$) against the second half ($\text{EF}_2$):

$$\text{Decoupling Rate } (Pw:Hr) = \left(\frac{\text{EF}_1 - \text{EF}_2}{\text{EF}_1}\right) \times 100\%$$

| Decoupling | Status | Coaching Verdict |
| :--- | :--- | :--- |
| **$< 3.0\%$** | **Elite Aerobic Base** | Cardiovascularly stable; stroke volume maintained throughout the run. |
| **$3.0\% - 5.0\%$** | **Aerobically Stable** | Established aerobic conditioning. Ready to progress duration or intensity. |
| **$5.0\% - 8.0\%$** | **Moderate Drift** | Normal on long runs ($>20\text{ km}$) or warm weather conditions. |
| **$> 8.0\%$** | **Aerobic Fatigue / Strain** | Significant cardiac drift. Indicates dehydration, heat strain, or aerobic base deficiency. |

---

### 3. VO₂ Max: Sub-Integer Telemetry (`fit.py`)
Garmin Connect truncates $\text{VO}_2\text{ max}$ to whole integers (e.g. `44`), hiding micro-adaptations and taper rebounds. The engine decodes raw Firstbeat metabolic data directly from binary `.FIT` files (**Message 140, Field 7**):

$$\text{VO}_2\text{ Max} = \frac{\text{Raw Value} \times 3.5}{65536.0}$$

This yields true two-decimal precision ($43.76 \to 44.03 \to 44.35$) to capture subtle fitness trends weeks before they reflect on consumer dashboards.

---

### 4. Fitness & Fatigue: CTL / ATL / TSB (`performance.py`)
Rooted in the **Impulse-Response Model** (Dr. Eric Banister, 1975) and the **Performance Management Chart** (Dr. Andrew Coggan & Hunter Allen, 2006).

1. **Daily Load:** Scales with Garmin EPOC Training Effect ($TE_{\text{aero}}$, $TE_{\text{ana}}$) and duration ($t_{\text{min}}$):
   $$\text{Load} = 8.0 \cdot (TE_{\text{aero}})^{2.2} + 12.0 \cdot (TE_{\text{ana}})^{1.8} + 0.4 \cdot t_{\text{min}}$$
   *(Fallback to HR Reserve TRIMP if TE is absent).*

2. **Exponentially Weighted Moving Averages (EWMA):**
   $$\text{CTL}_t = \text{Load}_t \cdot \alpha_{\text{CTL}} + \text{CTL}_{t-1} \cdot (1 - \alpha_{\text{CTL}}), \quad \alpha_{\text{CTL}} = \frac{2}{42 + 1}$$
   $$\text{ATL}_t = \text{Load}_t \cdot \alpha_{\text{ATL}} + \text{ATL}_{t-1} \cdot (1 - \alpha_{\text{ATL}}), \quad \alpha_{\text{ATL}} = \frac{2}{7 + 1}$$
   $$\text{TSB}_t = \text{CTL}_t - \text{ATL}_t$$

| Metric | Scientific Concept | Time Constant ($\tau$) | Status Interpretation |
| :--- | :--- | :--- | :--- |
| **CTL (Fitness)** | Long-term adaptations (capillaries, mitochondria) | 42 days | Higher values indicate deeper aerobic conditioning base. |
| **ATL (Fatigue)** | Short-term acute metabolic and neuromuscular stress | 7 days | Rises quickly after hard workouts or high weekly mileage. |
| **TSB (Form)** | Balance between fitness and fatigue ($\text{CTL} - \text{ATL}$) | Daily | $>+15$ Peaked; $+5 \text{ to } +15$ Race Ready; $-10 \text{ to } +5$ Base; $-30 \text{ to } -10$ Productive Load; $<-30$ Overreaching. |

---

### 5. Injury Risk Prevention: ACWR (`performance.py`)
Based on **Dr. Tim Gabbett (2016)** (*British Journal of Sports Medicine*). Resolves the training-injury paradox: *high chronic loads protect against injury, but rapid acute spikes cause injuries.*

$$\text{ACWR} = \frac{\text{Acute Workload (Last 7 Days)}}{\text{Chronic Workload (Preceding 28 Days / 4)}}$$

| ACWR Range | Zone | Risk Diagnosis |
| :--- | :--- | :--- |
| **$< 0.80$** | **Underload** | Workload decaying; risk spikes if subsequent load jumps rapidly. |
| **$0.80 - 1.30$** | **The Sweet Spot** | **Lowest relative injury risk.** Safe load progression matched to tissue adaptation. |
| **$1.30 - 1.50$** | **Caution Zone** | Workload accelerating faster than connective tissue adapts. Prioritize sleep/recovery. |
| **$> 1.50$** | **The Danger Zone** | **2x to 4x relative soft-tissue injury risk.** Workload reduction or cross-training required. |

---

### 6. Pacing & Race Dynamics: Jack Daniels VDOT (`performance.py`)
Developed by **Dr. Jack Daniels & Jimmy Gilbert (1979)** in *Oxygen Power*. **VDOT** is an "effective" $\text{VO}_2\text{ max}$ that accounts for both aerobic capacity and biomechanical running economy.

1. **Velocity Oxygen Cost ($v$ in $\text{m/min}$):**
   $$VO_2(v) = -4.60 + 0.182258 \cdot v + 0.000104 \cdot v^2$$

2. **Fractional Utilization ($\theta(t)$ over duration $t$ in minutes):**
   $$\theta(t) = 0.80 + 0.1894393 \cdot e^{-0.012778 \cdot t} + 0.2989558 \cdot e^{-0.1932605 \cdot t}$$

3. **VDOT from Benchmark Performance ($d$ meters in $t$ minutes):**
   $$\text{VDOT} = \frac{VO_2(d / t)}{\theta(t)}$$

4. **Target Paces:** Derived by inverting the quadratic equation for target velocity $v$ at prescribed intensities:

| Training Zone | % of VDOT | Target Stimulus & Protocol |
| :--- | :--- | :--- |
| **Easy (E-Pace)** | $59\% - 74\%$ | Cellular adaptations, capillarization, fat oxidation. Recovery & long runs. |
| **Marathon (M-Pace)** | $75\% - 84\%$ | Race pace economy, glycogen sparing. Steady state runs ($10 - 25\text{ km}$). |
| **Threshold (T-Pace)** | $83\% - 88\%$ | Elevates lactate clearance speed. Sustainable for $\sim 60\text{ min}$ (tempo runs & cruise intervals). |
| **Interval (I-Pace)** | $95\% - 100\%$ | Stresses $\text{VO}_2\text{ max}$ & stroke volume. $3-5\text{ min}$ reps ($800\text{m}-1200\text{m}$) with equal jog rest. |
| **Repetition (R-Pace)** | $105\% - 120\%$ | Anaerobic speed, leg turnover, economy. Short reps ($200\text{m}-400\text{m}$) with full recovery. |

*Race predictions for 5K, 10K, Half, and Full Marathon are solved via numerical bisection for $f(t_{\text{pred}}) = \frac{VO_2(d / t_{\text{pred}})}{\theta(t_{\text{pred}})} - \text{VDOT} = 0$.*


---

## Testing

Run unit tests via `pytest`:
```bash
uv run --with pytest pytest
```
