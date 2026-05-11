# Looker Studio Setup Guide

Connect Looker Studio to your BigQuery dataset to build dashboards for electrical test data.

## 1. Connect to BigQuery

1. Go to [Looker Studio](https://lookerstudio.google.com/)
2. **Create** → **Data source**
3. Select **BigQuery** connector
4. Choose your project (`geyser-testing-department`), dataset (`electrical_tests`), and tables

## 2. Key Tables
| Table | Description |
|-------|-------------|
| `test_runs` | One row per CLK file: run_id, index_id, object_id, protocol, date_ymd, group_id, test_date |
| `cycle_metrics` | Per-cycle metrics (GNRL): capacity, energy, ESR, efficiencies |
| `step_metrics` | Per-step metrics: step_no, step_type, duration, voltage, current |
| `time_series` | Time-series (all RAW data, no downsampling): time_s, voltage_v, current_a, temperature_c, capacity_ah, energy_wh |
| `curves` | Downsampled curves (time-based): x=time_s, y=voltage_v, y2=current_a per point |
| `esr_computed` | Recomputed ESR at 10ms and 1s from RAW |

### Curves table columns (time-based)
| Column | Meaning |
|--------|---------|
| **x** | Time in seconds (global for full program; same as time_s) |
| **y** | Voltage (V) |
| **y2** | Current (A) |
| **seq** | Row index for ordering only; do not use for axes |

### Where downsampling occurs
| Table | Limit |
|-------|-------|
| `time_series` | None (all RAW data stored) |
| `curves` (CYCLING) | 150 points per cycle |
| `curves` (CV) | 1,500 points per cycle |

No downsampling: `test_runs`, `cycle_metrics`, `step_metrics`, `esr_computed`.

## 3. Suggested Dashboards

### ESR Trends
- **Data**: `cycle_metrics` + `test_runs`
- **Dimensions**: index_id, protocol_detected, cycle_no
- **Metrics**: esr_periodic_ohm, esr_charge_ohm, esr_discharge_ohm
- **Chart**: Time series or scatter (cycle_no vs ESR)

### Capacity / Energy per Batch
- **Data**: `cycle_metrics` + `test_runs`
- **Dimensions**: index_id, entity_type, protocol_detected
- **Metrics**: capacity_ah, energy_wh
- **Chart**: Bar chart by index_id or table

### Efficiency
- **Data**: `cycle_metrics`
- **Metrics**: coulombic_eff_pct, energy_eff_pct
- **Chart**: Time series by cycle_no

### Voltage overlay (cycles overlaid)
- **Data**: `cycling_overlay` custom query
- **Chart**: Scatter with lines
- **X-axis**: cycle_time_s (time within each cycle, 0–120 s)
- **Y-axis**: y (voltage)
- **Dimension**: series_label (one line per cycle)

### Full program profile (voltage/current vs time)
- **Data**: `cycling_profile` custom query
- **Chart**: Line or Scatter
- **X-axis**: x (global time in seconds)
- **Y-axis**: y (voltage), y2 (current)

### ESR Comparison (Cycler vs Recomputed)
- **Data**: `cycle_metrics` (esr_periodic_ohm) + `esr_computed` (esr_ohm)
- Join on run_id, index_id, cycle_no
- **Chart**: Scatter or table comparing values

## 4. Custom Queries for Looker Studio

Use these as **Custom query** data sources in Looker Studio. Replace `geyser-testing-department` and `electrical_tests` if your project/dataset differ.

### ESR_plot (ESR, ESRd and ESRc Plot per Cycle Per Cell/Block)

```sql
SELECT
  t.index_id,
  t.protocol_detected,
  t.date_ymd,
  t.group_id,
  t.test_date,
  c.cycle_no,
  c.esr_periodic_ohm,
  c.esr_charge_ohm,
  c.esr_discharge_ohm
FROM `geyser-testing-department.electrical_tests.test_runs` t
JOIN `geyser-testing-department.electrical_tests.cycle_metrics` c
  ON t.run_id = c.run_id
```

### capacity_energy_plot (Capacity / Energy per Batch)

```sql
SELECT
  t.index_id,
  t.entity_type,
  t.protocol_detected,
  t.date_ymd,
  t.group_id,
  t.test_date,
  c.cycle_no,
  c.capacity_ah,
  c.energy_wh,
  c.capacity_charge_ah,
  c.capacity_discharge_ah,
  c.energy_charge_wh,
  c.energy_discharge_wh
FROM `geyser-testing-department.electrical_tests.test_runs` t
JOIN `geyser-testing-department.electrical_tests.cycle_metrics` c
  ON t.run_id = c.run_id
```

**Bar charts:**
- Charge Ah vs cycle: X-axis = `cycle_no`, Y-axis = `capacity_charge_ah`
- Discharge Ah vs cycle: X-axis = `cycle_no`, Y-axis = `capacity_discharge_ah`
- Charge Wh vs cycle: X-axis = `cycle_no`, Y-axis = `energy_charge_wh`
- Discharge Wh vs cycle: X-axis = `cycle_no`, Y-axis = `energy_discharge_wh`

### efficiency_plot (Efficiency)

```sql
SELECT
  t.index_id,
  t.entity_type,
  t.protocol_detected,
  t.date_ymd,
  t.group_id,
  t.test_date,
  c.cycle_no,
  c.coulombic_eff_pct,
  c.energy_eff_pct
FROM `geyser-testing-department.electrical_tests.test_runs` t
JOIN `geyser-testing-department.electrical_tests.cycle_metrics` c
  ON t.run_id = c.run_id
```

### cycling_overlay (Voltage vs cycle time – cycles overlaid)

Overlay charge+discharge voltage for each cycle against time within cycle (0–120 s). Use **Scatter chart** with lines enabled (Looker Line charts treat X as categorical; Scatter uses continuous axes).

```sql
SELECT
  c.run_id,
  t.object_id,
  c.index_id,
  c.cycle_no,
  c.curve_type,
  c.x,
  c.y,
  c.y2,
  c.x - MIN(c.x) OVER (PARTITION BY c.run_id, c.cycle_no, c.curve_type) AS cycle_time_s,
  CONCAT('C', CAST(c.cycle_no AS STRING)) AS series_label,
  t.date_ymd,
  t.group_id,
  t.test_date,
  t.protocol_detected,
  t.entity_type
FROM `geyser-testing-department.electrical_tests.curves` c
LEFT JOIN `geyser-testing-department.electrical_tests.test_runs` t
  ON c.run_id = t.run_id
WHERE c.curve_type = 'CYCLING'
ORDER BY c.cycle_no, cycle_time_s
```

**Chart setup:** Scatter chart. X-axis = `cycle_time_s` (Average). Y-axis = `y` (Average). Dimension = `series_label`. Enable "Show lines" in Style. Set `cycle_time_s` to Number type in data source.

### cycling_profile (Full program voltage/current vs time)

Single timeline showing voltage and current over the entire test. X-axis = global time in seconds.

```sql
SELECT
  c.run_id,
  t.object_id,
  c.index_id,
  c.cycle_no,
  c.x,
  c.y,
  c.y2,
  t.date_ymd,
  t.group_id,
  t.test_date
FROM `geyser-testing-department.electrical_tests.curves` c
LEFT JOIN `geyser-testing-department.electrical_tests.test_runs` t
  ON c.run_id = t.run_id
WHERE c.curve_type = 'CYCLING'
ORDER BY c.x
```

**Chart setup:** Line or Scatter. X-axis = `x` (time in seconds). Y-axis = `y` (voltage), `y2` (current). Use dual Y-axis for voltage and current.

### ESR_comparison_plot (ESR Comparison: Cycler vs Recomputed)

```sql
SELECT
  t.index_id,
  t.entity_type,
  t.protocol_detected,
  t.date_ymd,
  t.group_id,
  t.test_date,
  c.cycle_no,
  c.esr_periodic_ohm AS esr_cycler_ohm,
  e.delay_s,
  e.esr_ohm AS esr_recomputed_ohm
FROM `geyser-testing-department.electrical_tests.test_runs` t
JOIN `geyser-testing-department.electrical_tests.cycle_metrics` c
  ON t.run_id = c.run_id
JOIN `geyser-testing-department.electrical_tests.esr_computed` e
  ON c.run_id = e.run_id AND c.index_id = e.index_id AND c.cycle_no = e.cycle_no
```

---

## 5. Example SQL (Optional Views)

If you prefer pre-built views in BigQuery:

```sql
-- ESR by index and cycle
CREATE OR REPLACE VIEW electrical_tests.v_esr_by_cycle AS
SELECT
  t.index_id,
  t.protocol_detected,
  c.cycle_no,
  c.esr_periodic_ohm,
  c.capacity_ah,
  c.energy_wh
FROM electrical_tests.cycle_metrics c
JOIN electrical_tests.test_runs t ON c.run_id = t.run_id;

-- Capacity summary per run
CREATE OR REPLACE VIEW electrical_tests.v_capacity_summary AS
SELECT
  t.index_id,
  t.entity_type,
  t.protocol_detected,
  MAX(c.cycle_no) AS max_cycle,
  AVG(c.capacity_ah) AS avg_capacity_ah,
  AVG(c.coulombic_eff_pct) AS avg_coulombic_eff
FROM electrical_tests.cycle_metrics c
JOIN electrical_tests.test_runs t ON c.run_id = t.run_id
GROUP BY t.run_id, t.index_id, t.entity_type, t.protocol_detected;
```

## 6. Filters

Add report-level filters for:
- `test_date` (calendar – Date range control)
- `group_id` (G01, G02, B01, etc.)
- `index_id` (specific cell/block)
- `protocol_detected` (STANDARD_CELL, BLOCK_SCANNING, BLOCK_CYCLING, CYCLABILITY, UNKNOWN)
- `entity_type` (cell, block)

For **cycling_overlay**, add filters on `run_id`, `index_id` to select a cell/block.

For **cycling_profile**, add filters on `run_id`, `index_id` to select a cell/block.

For **ESR_comparison_plot**, add a filter on `delay_s` (0.01 or 1.0) to compare at a specific delay.

### Chart setup quick reference

| Data source | Chart type | Dimension | X-axis | Y-axis | Notes |
|-------------|-----------|-----------|--------|--------|-------|
| ESR_trends | Bar / Scatter | cycle_no | cycle_no | esr_periodic_ohm | Filter by index_id for one cell |
| capacity_energy_plot | Bar / Table | index_id, cycle_no | cycle_no | capacity_charge_ah, capacity_discharge_ah, energy_charge_wh, energy_discharge_wh | Charge/discharge Ah and Wh per cycle |
| efficiency_plot | Bar / Scatter | cycle_no | cycle_no | coulombic_eff_pct, energy_eff_pct | Filter by index_id |
| cycling_overlay | Scatter | series_label | cycle_time_s | y | Voltage vs cycle time; enable "Show lines" |
| cycling_profile | Line / Scatter | — | x | y, y2 | Full program voltage/current vs time |
| ESR_comparison_plot | Scatter / Table | cycle_no | esr_cycler_ohm | esr_recomputed_ohm | Filter delay_s for one delay |
