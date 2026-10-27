# Sports Science Reference

How each metric in `ricelo-running-science` is calculated, with its source. Back to the [README](../README.md).

---

### 1. Efficiency: Efficiency Factor (EF) (`performance.py`)
Popularized by endurance physiologist Joe Friel, **Efficiency Factor (EF)** quantifies mechanical output produced per cardiovascular beat (speed-to-heart-rate ratio):

$$\text{EF} = \frac{\text{Velocity (m/s)}}{\text{Heart Rate (bpm)}} \times 1000$$

A rising EF across weeks at similar training paces indicates improved running economy and aerobic engine development—enabling you to run faster at the same heart rate or maintain pace with less cardiac strain.

---

### 2. Aerobic Efficiency: Cardiac Drift & Decoupling (`fit.py`)
During steady aerobic running, heart rate often drifts upward despite constant pace due to cutaneous vasodilation, dehydration, and motor unit fatigue. Aerobic Decoupling evaluates cardiovascular stability by comparing the first half ($\text{EF}_1$) against the second half ($\text{EF}_2$) of the moving time, using the FIT file's speed and heart-rate samples (pauses excluded; needs at least 20 minutes):

$$\text{Decoupling Rate } (Pa:Hr) = \left(\frac{\text{EF}_1 - \text{EF}_2}{\text{EF}_1}\right) \times 100\%$$

| Decoupling | Status | Coaching Verdict |
| :--- | :--- | :--- |
| **$< 5.0\%$** | **Aerobically Stable** | Cardiovascularly stable; established aerobic conditioning. Ready to progress duration or intensity. |
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
   $$\text{CTL}_t = \text{Load}_t \cdot \alpha_{\text{CTL}} + \text{CTL}_{t-1} \cdot (1 - \alpha_{\text{CTL}}), \quad \alpha_{\text{CTL}} = 1 - e^{-1/42}$$
   $$\text{ATL}_t = \text{Load}_t \cdot \alpha_{\text{ATL}} + \text{ATL}_{t-1} \cdot (1 - \alpha_{\text{ATL}}), \quad \alpha_{\text{ATL}} = 1 - e^{-1/7}$$
   $$\text{TSB}_t = \text{CTL}_t - \text{ATL}_t$$

   The model is warmed up over 126 days (3 CTL time constants) before the reported window.

| Metric | Scientific Concept | Time Constant ($\tau$) | Status Interpretation |
| :--- | :--- | :--- | :--- |
| **CTL (Fitness)** | Long-term adaptations (capillaries, mitochondria) | 42 days | Higher values indicate deeper aerobic conditioning base. |
| **ATL (Fatigue)** | Short-term acute metabolic and neuromuscular stress | 7 days | Rises quickly after hard workouts or high weekly mileage. |
| **TSB (Form)** | Balance between fitness and fatigue ($\text{CTL} - \text{ATL}$) | Daily | $>+15$ Very Fresh; $+5 \text{ to } +15$ Fresh / Race Ready; $-10 \text{ to } +5$ Optimal Base; $-30 \text{ to } -10$ Productive Load; $<-30$ High Volume Accumulation. |

---

### 5. Injury Risk Prevention: ACWR (`performance.py`)
Based on **Dr. Tim Gabbett (2016)** (*British Journal of Sports Medicine*). Resolves the training-injury paradox: *high chronic loads protect against injury, but rapid acute spikes cause injuries.*

$$\text{ACWR} = \frac{\text{Acute Workload (Last 7 Days)}}{\text{Chronic Workload (Last 28 Days / 4)}}$$

The chronic window includes the acute week (the "coupled" ACWR).

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

3. **VDOT from Benchmark Performance ($d$ meters in $t$ minutes):** the benchmark is the best 5K, 10K, half or marathon effort of the last 365 days, preferring runs tagged as races in Garmin Connect.
   $$\text{VDOT} = \frac{VO_2(d / t)}{\theta(t)}$$

4. **Target Paces:** Derived by inverting the quadratic equation for target velocity $v$ at prescribed intensities:

| Training Zone | % of VDOT | Target Stimulus & Protocol |
| :--- | :--- | :--- |
| **Easy (E-Pace)** | $62\% - 70\%$ | Cellular adaptations, capillarization, fat oxidation. Recovery & long runs. |
| **Marathon (M-Pace)** | $80\%$ | Race pace economy, glycogen sparing. Steady state runs ($10 - 25\text{ km}$). |
| **Threshold (T-Pace)** | $88\%$ | Elevates lactate clearance speed. Sustainable for $\sim 60\text{ min}$ (tempo runs & cruise intervals). |
| **Interval (I-Pace)** | $97.5\%$ | Stresses $\text{VO}_2\text{ max}$ & stroke volume. $3-5\text{ min}$ reps ($800\text{m}-1200\text{m}$) with equal jog rest. |
| **Repetition (R-Pace)** | $105\%$ | Anaerobic speed, leg turnover, economy. Short reps ($200\text{m}-400\text{m}$) with full recovery. |

*Race predictions for 5K, 10K, Half, and Full Marathon are solved via numerical bisection for $f(t_{\text{pred}}) = \frac{VO_2(d / t_{\text{pred}})}{\theta(t_{\text{pred}})} - \text{VDOT} = 0$.*
