---
name: Update Plan RAW Time-Series
overview: Add the RAW time-series storage requirement and time concatenation rule to the Geyser Cycler Analysis Tool plan document so it is explicitly documented.
todos: []
isProject: false
---

# Update Plan: RAW Time-Series Storage in BigQuery

## Current State

**Plan document** ([.cursor/plans/geyser_cycler_analysis_tool_8562c610.plan.md](H:\Shared drives\R&D_Mikkeli\Zawar Qureshi\Geyser Testing Departmentcursor\plans\geyser_cycler_analysis_tool_8562c610.plan.md)):

- Section 5 lists `test_runs`, `cycle_metrics`, `step_metrics`, `esr_computed`, and `curves`
- **No `time_series` table** is documented
- **No requirement** for storing full RAW time-series (time, V, I, T, Ah, Wh) per sample
- **No mention** of time concatenation (RAW files reset time per step; we need continuous time from program start to end)

**Implementation** (already done):

- [geyser_tool/parsers/raw_reader.py](Tool/geyser_tool/parsers/raw_reader.py): `add_continuous_time()` concatenates time across steps within each RAW file
- [geyser_tool/upload/bigquery.py](Tool/geyser_tool/upload/bigquery.py): `build_time_series_rows()` stores time_s, voltage_v, current_a, temperature_c, capacity_ah, energy_wh with step_no, step_type; adds cycle offset for continuous time across files
- `time_series` table exists in setup_bigquery.py

---

## Edits to the Plan

### 1. Add Section 4D: RAW Time-Series Storage (in Section 4: Metric extraction strategy)

Insert a new subsection **4D** after 4C (Protocol-specific metric rules):

**4D. RAW time-series storage (all RAW files)**

For **all** RAW files associated with each CLK run, the tool stores the full time-series in BigQuery:

- **Per-sample data**: time, voltage, current, temperature, capacity (Ah), energy (Wh)
- **Per-step context**: `step_no`, `step_type` (e.g. CCC, DCC, SNU) for each sample
- **Time concatenation**: In RAW files, `Time,s` resets to zero at each step. The tool **concatenates time** so that `time_s` is monotonically increasing from program start to end:
  - Within each RAW file: add cumulative offset per step (sum of previous step durations)
  - Across multiple RAW files (per cycle): add cycle-based offset so the full program timeline is continuous

This enables graph building (V–t, I–t, Ah–t, etc.) over the entire test program.

### 2. Add Section 5E: `time_series` table (renumber existing 5E/5F)

Insert a new table section **5E** for `time_series`, and renumber:

- Current 5E (`curves`) → 5F
- Current 5F (Deduplication) → 5G

**5E. `time_series` — full RAW time-series per sample**


| Column          | Type    | Description                                        |
| --------------- | ------- | -------------------------------------------------- |
| `run_id`        | STRING  | Links to test_runs                                 |
| `index_id`      | STRING  | e.g. 260203_G01_033                                |
| `cycle_no`      | INT64   | Cycle number                                       |
| `step_no`       | INT64   | Step number (e.g. 5, 6)                            |
| `step_type`     | STRING  | Canonical step type (CCC, DCC, SNU, etc.)          |
| `seq`           | INT64   | Sample sequence within run                         |
| `time_s`        | FLOAT64 | **Concatenated** time from program start (seconds) |
| `voltage_v`     | FLOAT64 | Voltage (V)                                        |
| `current_a`     | FLOAT64 | Current (A)                                        |
| `temperature_c` | FLOAT64 | Temperature (°C)                                   |
| `capacity_ah`   | FLOAT64 | Capacity (Ah)                                      |
| `energy_wh`     | FLOAT64 | Energy (Wh)                                        |


### 3. Update Phase 2 description (Section 7)

In the Phase 2 bullet list, add an explicit item:

- **RAW time-series storage**: Store time, voltage, current, temperature, Ah, Wh for all RAW files, with step_no and step_type; time concatenated from program start to end.

### 4. Update Architecture (Section 6)

In the `raw_reader.py` bullet, clarify:

- `raw_reader.py`: Parse RAW files into time-series DataFrames; **concatenate time across steps**; output time_continuous_s, step_no, step_type.

---

## Summary of Changes


| Location          | Change                                                                |
| ----------------- | --------------------------------------------------------------------- |
| Section 4         | Add 4D: RAW time-series storage requirement + time concatenation rule |
| Section 5         | Add 5E: `time_series` table schema; renumber 5E→5F, 5F→5G             |
| Section 6         | Clarify raw_reader time concatenation                                 |
| Section 7 Phase 2 | Add RAW time-series storage bullet                                    |


