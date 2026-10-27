# Sports Science Reference

How each metric in `ricelo-running-science` is calculated, with its source. Back to the [README](../README.md).

---

### 1. Efficiency: Efficiency Factor (EF) (`performance.py`)
Popularized by endurance physiologist Joe Friel, **Efficiency Factor (EF)** quantifies mechanical output produced per cardiovascular beat (speed-to-heart-rate ratio):

```
EF = speed (m/s) ÷ heart rate (bpm) × 1000
```

A rising EF across weeks at similar training paces indicates improved running economy and aerobic engine development—enabling you to run faster at the same heart rate or maintain pace with less cardiac strain.

---

### 2. Aerobic Efficiency: Cardiac Drift & Decoupling (`fit.py`)
During steady aerobic running, heart rate often drifts upward despite constant pace due to cutaneous vasodilation, dehydration, and motor unit fatigue. Aerobic Decoupling evaluates cardiovascular stability by comparing the first half (EF1) against the second half (EF2) of the moving time, using the FIT file's speed and heart-rate samples (pauses excluded; needs at least 20 minutes):

```
Decoupling (Pa:Hr) = (EF1 − EF2) ÷ EF1 × 100%
```

*Pa:Hr* stands for pace to heart rate: how your pace relates to your heart rate in each half of the run. If the pace holds but heart rate climbs, the two "decouple". (Cycling uses *Pw:Hr*, power to heart rate.)

| Decoupling | Status | Coaching Verdict |
| :--- | :--- | :--- |
| **under 5%** | **Aerobically Stable** | Cardiovascularly stable; established aerobic conditioning. Ready to progress duration or intensity. |
| **5–8%** | **Moderate Drift** | Normal on long runs (over 20 km) or in warm weather. |
| **over 8%** | **Aerobic Fatigue / Strain** | Significant cardiac drift. Indicates dehydration, heat strain, or aerobic base deficiency. |

---

### 3. VO₂ Max: Sub-Integer Telemetry (`fit.py`)
Garmin Connect rounds VO₂ max to a whole number (e.g. 44.38 shows as `44`), hiding small changes. The engine decodes raw Firstbeat metabolic data directly from binary `.FIT` files (**Message 140, Field 7**):

```
VO2 max = raw value × 3.5 ÷ 65536
```

This gives two decimals (for example 43.9 → 44.2 → 44.4), so you can see a trend before the whole number changes.

---

### 4. Fitness & Fatigue: CTL / ATL / TSB (`performance.py`)
Rooted in the **Impulse-Response Model** (Dr. Eric Banister, 1975) and the **Performance Management Chart** (Dr. Andrew Coggan & Hunter Allen, 2006).

1. **Daily Load:** each activity's load comes from Garmin's aerobic and anaerobic Training Effect (TE) and its duration in minutes, summed per day. This is ricelo's own scale, not Garmin's Training Load or TSS.
   ```
   load = 8.0 × aerobic_TE^2.2 + 12.0 × anaerobic_TE^1.8 + 0.4 × minutes
   ```
   Without Training Effect, it falls back to a heart-rate estimate that assumes a resting HR of 50 and a max HR of 190, and without heart rate, to duration alone:
   ```
   load = 1.5 × minutes × (average HR − 50) ÷ 140
   load = 0.8 × minutes
   ```

2. **Exponentially weighted moving averages**, one value per day:
   ```
   CTL(today) = load(today) × a_CTL + CTL(yesterday) × (1 − a_CTL),   a_CTL = 1 − e^(−1/42)
   ATL(today) = load(today) × a_ATL + ATL(yesterday) × (1 − a_ATL),   a_ATL = 1 − e^(−1/7)
   TSB(today) = CTL(today) − ATL(today)
   ```
   The model is warmed up over 126 days (3 CTL time constants) before the reported window.

| Metric | Scientific Concept | Time Constant | Status Interpretation |
| :--- | :--- | :--- | :--- |
| **CTL (Fitness)** | Long-term adaptations (capillaries, mitochondria) | 42 days | Higher values indicate deeper aerobic conditioning base. |
| **ATL (Fatigue)** | Short-term acute metabolic and neuromuscular stress | 7 days | Rises quickly after hard workouts or high weekly mileage. |
| **TSB (Form)** | Balance between fitness and fatigue (CTL − ATL) | Daily | over +15 Very Fresh; +5 to +15 Fresh / Race Ready; −10 to +5 Optimal Base; −30 to −10 Productive Load; under −30 High Volume Accumulation. |

---

### 5. Injury Risk Prevention: ACWR (`performance.py`)
Based on **Dr. Tim Gabbett (2016)** (*British Journal of Sports Medicine*). Resolves the training-injury paradox: *high chronic loads protect against injury, but rapid acute spikes cause injuries.*

```
ACWR = acute load (last 7 days) ÷ chronic load (last 28 days ÷ 4)
```

The chronic window includes the acute week (the "coupled" ACWR).

| ACWR Range | Zone | Risk Diagnosis |
| :--- | :--- | :--- |
| **under 0.80** | **Underload** | Workload decaying; risk spikes if subsequent load jumps rapidly. |
| **0.80–1.30** | **The Sweet Spot** | **Lowest relative injury risk.** Safe load progression matched to tissue adaptation. |
| **1.30–1.50** | **Caution Zone** | Workload accelerating faster than connective tissue adapts. Prioritize sleep/recovery. |
| **over 1.50** | **The Danger Zone** | **Markedly higher injury risk in published studies.** Reduce workload or cross-train. |

ACWR is a rough guide, not a diagnosis. Later research (for example Impellizzeri et al., 2020) questions how well it predicts injury for an individual athlete.

---

### 6. Pacing & Race Dynamics: Jack Daniels VDOT (`performance.py`)
Developed by **Dr. Jack Daniels & Jimmy Gilbert (1979)** in *Oxygen Power*. **VDOT** is an "effective" VO₂ max that accounts for both aerobic capacity and biomechanical running economy.

1. **Oxygen cost of running at velocity v** (metres per minute):
   ```
   VO2(v) = −4.60 + 0.182258 × v + 0.000104 × v²
   ```

2. **Fraction of VO₂ max sustainable for t minutes:**
   ```
   θ(t) = 0.80 + 0.1894393 × e^(−0.012778 × t) + 0.2989558 × e^(−0.1932605 × t)
   ```

3. **VDOT from a benchmark of d metres in t minutes.** The benchmark is the best 5K, 10K, half or marathon effort of the last 365 days, preferring runs tagged as races in Garmin Connect.
   ```
   VDOT = VO2(d ÷ t) ÷ θ(t)
   ```

4. **Target paces:** derived by solving the oxygen-cost equation for the velocity v at each intensity. Daniels gives ranges (Easy 59–74%, Marathon 75–84%, Threshold 83–88%, Interval 95–100%, Repetition 105–120%); ricelo uses one point within each:

| Training Zone | % of VDOT | Target Stimulus & Protocol |
| :--- | :--- | :--- |
| **Easy (E-Pace)** | 62–70% | Cellular adaptations, capillarization, fat oxidation. Recovery & long runs. |
| **Marathon (M-Pace)** | 80% | Race pace economy, glycogen sparing. Steady state runs (10–25 km). |
| **Threshold (T-Pace)** | 88% | Elevates lactate clearance speed. Sustainable for about 60 min (tempo runs & cruise intervals). |
| **Interval (I-Pace)** | 97.5% | Stresses VO₂ max & stroke volume. 3–5 min reps (800–1200 m) with equal jog rest. |
| **Repetition (R-Pace)** | 105% | Anaerobic speed, leg turnover, economy. Short reps (200–400 m) with full recovery. |

Race predictions for 5K, 10K, half and full marathon are found by bisection: the predicted time t is the one where `VO2(d ÷ t) ÷ θ(t) = VDOT`.
