---
name: Geyser Cycler Analysis Tool
overview: A protocol-aware Python analysis tool that ingests CLK/RAW cycler logs from Geyser's ASK/ACK75 analyzers, detects protocol from the program header, ingests CLK metrics, stores full RAW time_series, boundary-based ESR for selected protocols, Looker-friendly curves (no dQ/dV), uploads to BigQuery (optional GCS staging), fingerprints, Tkinter GUI + CLI, Looker dashboards. See § "Permanently out of scope" for items excluded from all future "what's left" summaries.
todos:
  - id: phase1-parsers
    content: "Phase 1: Build header parser (metadata, program steps, Object ID) + CLK tabular reader"
    status: completed
  - id: phase1-protocol
    content: "Phase 1: Protocol detection — YAML registry (STANDARD_CELL, BLOCK_SCANNING, BLOCK_CYCLING); CYCLABILITY gated in detector.py (>20 cycles, cell vs block discharge spelling); UNKNOWN fallback"
    status: completed
  - id: phase1-ids
    content: "Phase 1: Build ID parser (index_id extraction, entity_type from path) and naming convention logic"
    status: completed
  - id: phase1-bigquery
    content: "Phase 1: BigQuery schema setup script + upload module (test_runs, cycle_metrics, step_metrics) with dedup"
    status: completed
  - id: phase1-auth
    content: "Phase 1: Google Cloud auth module (service account) + local Parquet staging"
    status: completed
  - id: phase1-gui
    content: "Phase 1: Tkinter app (folder picker, progress, Update BigQuery button, log viewer) + CLI mirror"
    status: completed
  - id: phase1-looker
    content: "Phase 1: Basic Looker Studio dashboards (ESR trends, capacity/energy per batch, efficiency)"
    status: completed
  - id: phase1-tests
    content: ~~Automated pytest~~ — not in scope (permanent); do not list as backlog
    status: cancelled
  - id: phase2-raw
    content: "Phase 2: RAW file reader + boundary-based ESR (delays 0.01/1.0 row semantics) + configurable raw_ts gap"
    status: completed
  - id: phase2-curves
    content: "Phase 2: Curve generation (CV/SNU, Cycling V–t+I); curves BQ table; per-step downsampling — dQ/dV not in scope"
    status: completed
  - id: phase3-ocv
    content: "Optional deferred: PyInstaller EXE for lab PCs — DCIR / explorer / extra dashboards not in scope"
    status: pending
  - id: phase3-incremental-upload
    content: "Phase 3: Per-table fingerprint - smart skip when fingerprints match; --force override"
    status: completed
  - id: phase3-parquet-gcs
    content: "Phase 3: Parquet local -> GCS -> BigQuery load (memory-efficient, retry from GCS)"
    status: completed
  - id: phase3-split-stage-upload
    content: "Phase 3: Split stage and upload flow - Stage to Parquet only, then Upload batch to GCS/BigQuery"
    status: completed
  - id: phase3-dynamic-max-points
    content: "Optional: tighten time_series row policy if warehouse cost dominates (full fidelity kept by default)"
    status: pending
  - id: docs-sync
    content: Maintain README / LOOKER_SETUP / plan changelog when altering RAW, curves, or ESR
    status: completed
isProject: false
---

# Geyser Cycler Analysis Tool -- Revised Plan

## Living document — keeping this plan up to date

This file is the **engineering record**: phased goals, backlog, architecture notes, and an **implementation changelog**. It is meant to drift **only when someone updates it**.

**When you change behaviour, UX, schemas, or protocol rules**, refresh these in tandem:


| Change type                                    | Update                                                                                                                                                                              |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| User-visible flows                             | [Tool/README.md](../Tool/README.md) — functional overview, CLI/GUI flags                                                                                                            |
| Looker dimensions / metric names               | [Tool/docs/LOOKER_SETUP.md](../Tool/docs/LOOKER_SETUP.md)                                                                                                                           |
| Protocol keywords (`STANDARD_CELL`, `BLOCK_`*) | [Tool/geyser_tool/protocol/registry.yaml](../Tool/geyser_tool/protocol/registry.yaml)                                                                                               |
| CYCLABILITY thresholds, regexp, precedence     | [Tool/geyser_tool/protocol/detector.py](../Tool/geyser_tool/protocol/detector.py)                                                                                                   |
| Phased scope / backlog / “what landed when”    | **This plan** — add a dated entry under §11 Implementation Changelog; adjust frontmatter todos if needed; **exclude § Permanently out of scope items** from “what’s left” summaries |


**Superseded content:** Older sections still mention labels like `**VTT_TEST1_BLOCK`** or scored pattern matching—they are retained as narrative context unless removed. Prefer the **changelog** and `**Tool/`** docs for truth about the **running** codebase.

### Permanently out of scope (authoritative)

The following are **not** part of this product roadmap. They must **not** be repeated when answering “what is left in the plan” or similar backlog questions:


| Item                           | Notes                                                                                                        |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| ~~**Automated pytest**~~       | No CI test suite commitment; optional ad-hoc scripts only.                                                   |
| ~~**Protocol “explorer” UI**~~ | No dedicated explorer CLI/GUI.                                                                               |
| ~~**DCIR extractor**~~         | No DCIR-from-RAW pipeline in this tool.                                                                      |
| ~~**dQ/dV**~~                  | Not computed in `curves` / analysis; CYCLING + CV only.                                                      |
| ~~**Extra registry labels**~~  | e.g. Reference GCD, FCR-D, legacy SOP-specific rows — not adding detection rows unless explicitly re-scoped. |


**Still optionally deferred (may appear in “what’s left” when relevant):** PyInstaller EXE; optional `time_series` cost cap (`phase3-dynamic-max-points`); ongoing doc/config hygiene.

---

## 1. What the tool does (high level)

```mermaid
flowchart LR
  subgraph local [Windows Lab PC]
    LocalData["D:\Electrical_tests\<br/>Cells/ + Blocks/"]
    GUI["GUI App<br/>(Tkinter)"]
    Engine["Parse + Detect<br/>+ Compute"]
    Staging["Local Parquet<br/>(staging cache)"]
  end
  subgraph cloud [Google Cloud]
    GCS["GCS bucket<br/>(optional)"]
    BQ["BigQuery<br/>datasets + tables"]
    Looker["Looker Studio<br/>dashboards"]
  end
  LocalData --> GUI --> Engine --> Staging --> GCS
  Staging --> BQ
  GCS --> BQ
  BQ --> Looker
```



- **Input**: CLK summary files and RAW per-cycle files from `D:\Electrical_tests\Cells\` and `D:\Electrical_tests\Blocks\`. Folder structures are **usually** `YYYY\MM\DD\N_Gnn\NNN\`, but the tool primarily infers IDs/dates from the `Object:` header and `YYMMDD` patterns so it remains robust when `N_Gnn` or `NNN` are missing or renamed.
- **Processing**: Parse metadata headers, detect protocol, extract per-cycle/step metrics from CLK, recompute boundary-based ESR from RAW when protocol + program gates pass, generate curve points (no dQ/dV).
- **Output**: BigQuery tables (canonical DB), Looker Studio dashboards (analytics), local Parquet staging cache.
- **Trigger**: User clicks "Update BigQuery" in a small Windows GUI, or runs a CLI command.

---

## 2. Key insight from your actual files

Your cycler (ACK75.10.20.12 / ACK75.48.1750.1) already stores **the full program definition in every file header** (both CLK and RAW). For example:

```
Analyzer: ACK75.10.20.12  IP: 192.168.100.141  Channel: 4
Object: 260203_1_G01_033_C_001
Program:
 1 Charge CC 2.5A to 0.5V or 10000s. Period ESR: 1s. Duration ESR: 1000Hz.
 2 Scanning U 10mV/s frm 0.5V to 1.72V. I subrange 10A.
 3 Scanning U 10mV/s frm 1.72V to 0.5V. I subrange 10A.
 4 Cycle to step 2   3 times.
 5 Charge CC 4.5A to 1.7V or 10000s. Period ESR: 1s. Duration ESR: 1000Hz.
 6 Discharge CC 4.5A to 0.85V or 10000s. ESR: Not meas.
 7 Cycle to step 5   20 times.
...
```

This is the **protocol fingerprint**. The tool will parse these program lines into a structured `ProgramDefinition`, then match it against known protocol templates (and also handle unknown protocols gracefully).

Additionally, the CLK file already contains **pre-computed per-step and per-cycle metrics**: `Q,Ah`, `E,Wh`, `C,F`, `ERc,mR`, `ERd,mR`, `ESR,mR`, `Ilk,A`, `EFq,%`, `EFe,%`. These are **trusted** and will be ingested directly. ESR recomputation from RAW is a separate, optional step for custom time windows (e.g., ESR at 10ms and 1s per your SOP Section 3D).

---

## 2.1 Step tags and modes (from manual + data)

The ASK/ACK75 analyzers use a rich set of step markers in both RAW and CLK files. The tool will normalize these into a small set of canonical `step_type` values via an explicit mapping table. The following markers are expected (from the ASC75 manual and your `Electrical_tests` data):

- **Constant current / voltage / power / resistance steps**
  - Charge: `CHCC`, `CCC` (charge constant current), `CHCV` (charge constant voltage), `CHCP` (charge constant power)
  - Discharge: `DCHCC`, `DCCC`, `DCC` (discharge constant current), `DCV` / `DCHCV` (discharge constant voltage), `DCHCP` (discharge constant power), `DCHCR` / `DCR` (discharge to constant resistance)
- **Scans / sweeps**
  - `SNU` (voltage scan), `SNI` (current scan), `SNP` (power scan), `SNR` (resistance scan)
- **Relaxation and logging**
  - `RLX` / `RLAX` (relaxation), `LGU` (voltage logger)
- **Table-driven profiles**
  - `TBU` / `Table U` (voltage table), `TBI` / `Table I` (current table), `TBP` / `Table P` / `TBLP` (power table), `TBR` / `Table R` (resistance table)
- **Pulse modes**
  - `IPI` (current pulse mode), `IPU` (voltage pulse mode), `IPP` (power pulse mode), `IPR` (resistance pulse mode)
- **Other**
  - `MPPT` (maximum power point tracking), `U recorder` / `LGU` (voltage recorder), `Pause`, plus summary `GNRL` lines.

All these markers will be mapped into canonical `step_type` enums (e.g., `CCC`, `DCC`, `CV_CHARGE`, `CV_DISCHARGE`, `SCAN_V`, `RELAX`, `LOGGER`, `TABLE_POWER`, `PULSE_CURRENT`, etc.) that the protocol detector and metric logic use. This makes the system robust as new protocols are introduced while still leveraging the analyzer’s native tagging.

---

## 3. Protocol detection engine

### 3.0 Implemented labels (canonical; 2026-05-11)

Values written to BigQuery as `**test_runs.protocol_detected`**:


| Label            | Where defined                                                                | Summary                                                                                                                                                                                                                                                                                         |
| ---------------- | ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CYCLABILITY`    | [detector.py](../Tool/geyser_tool/protocol/detector.py) runs **before** YAML | **Cell path:** `Charge CC`, correct `Discharge CC`, regexp `Cycle to step <step> <n> times` with `**n > 20`**, and joined program text must not contain typo `Discarge CC`. Block path: `Charge CC`, typo `Discarge CC`, line `Preset number of cycles: N` with `**N > 20`**. Confidence `0.5`. |
| `STANDARD_CELL`  | [registry.yaml](../Tool/geyser_tool/protocol/registry.yaml) (first row)      | `Scanning U` + `Charge CC` + `Discharge CC` + `Charge CV` + (`Rest` **or** `Logger U`).                                                                                                                                                                                                         |
| `BLOCK_SCANNING` | registry (second row)                                                        | `Scanning U` + `Preset number of cycles`.                                                                                                                                                                                                                                                       |
| `BLOCK_CYCLING`  | registry (third row)                                                         | `Discarge CC` + `Rest` + `Preset number of cycles` (any `N`, including ≤ 20).                                                                                                                                                                                                                   |
| `UNKNOWN`        | implicit                                                                     | Nothing matched YAML + detector fallbacks (if registry absent).                                                                                                                                                                                                                                 |


`ProgramStep.params` / `cycle_ref` in [program_model.py](../Tool/geyser_tool/protocol/program_model.py) support future structured parsing; today `**pipeline._program_from_header`** fills `**step_num`** and `**mode**` only — detection is substring / regexp matching on concatenated `**mode**` text ([detector.detect_protocol](../Tool/geyser_tool/protocol/detector.py)).

### 3A. Target data model for program steps

Each program header is parsed into a list of `ProgramStep` objects. **Implementation note:** `**params`** and `**cycle_ref`** are ~~partial~~ / ~~future-ready~~ — today **`pipeline`** fills **`step_num`** and **`mode`** text only (detection operates on concatenated **`mode`** strings).

```python
@dataclass
class ProgramStep:
    step_num: int
    mode: str          # "Charge CC", "Discharge CC", "Scanning U", "Charge CV", "Logger U", etc.
    params: dict       # current_a, voltage_v, scan_rate, duration_s, esr_config, etc.
    cycle_ref: tuple   # (target_step, n_times) if this is a "Cycle to step X, N times"
```

### ~~3B. Historical SOP-aligned pattern taxonomy~~ — **LEGACY / ARCHIVE ONLY**

> **Withdrawn as product spec.** The **only** `protocol_detected` values emitted by ingestion are **`CYCLABILITY`**, **`STANDARD_CELL`**, **`BLOCK_SCANNING`**, **`BLOCK_CYCLING`**, **`UNKNOWN`** (see §3.0). The table below is **historical SOP cross-reference only** — rows marked ~~not shipped~~ align with ~~**extra registry labels**~~ / ~~**DCIR**~~ under **§ Permanently out of scope**.


| Protocol Label                                                         | Step Sequence Pattern                                                                           | Source Doc                        |
| ---------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- | --------------------------------- |
| `STANDARD_CELL`                                                        | High-level CCC/SNU/DCC sequencing (conceptual SOP §3C) *(aligns with implemented label §3.0)*     | SOP Section 3                     |
| ~~`VTT_TEST1_BLOCK`~~                                                  | ~~*(label retired in code)* replaced by **`BLOCK_SCANNING`** / **`BLOCK_CYCLING`**~~            | ~~Was ToS §7 wording - Discontinued~~ |
| ~~`VTT_FCRD_NONZINC`~~ *(**not implemented**)*                         | ~~`CCC -> CCV -> TableP -> DCC`~~                                                               | ~~ToS Section 8~~                 |
| `CYCLABILITY` *(aligns with implemented label §3.0)*                    | SOP-style cyclability narrative; **code** uses **`> 20`** cycles + cell vs block discharge spelling (§3.0) | SOP Section 5                     |
| ~~`REFERENCE_GCD`~~ *(**not implemented**)*                            | ~~`CCC(0.85->1.70V) -> DCC(1.72->0.80V) -> Rest(2min) -> Cycle(3)`~~                             | ~~SOP Section 5~~                 |
| ~~`DCIR_SOC`~~ *(**not implemented** — DCIR tooling out of scope)*     | ~~Complex pulse sequence with rest~~                                                             | ~~SOP Section 6~~                 |


If **no** implemented rule matches (§3.0 + YAML + fallbacks), the protocol is `**UNKNOWN`**; CLK / RAW metrics are still ingested.

### 3C. Why this is robust to protocol changes

- **YAML** entries for `STANDARD_CELL` / `BLOCK_*` are editable without changing Python. `**CYCLABILITY`** requires code changes in `**detector.py`** when thresholds or shapes change.
- Unknown protocols still get full metric ingestion from CLK.
- The program header text is also stored verbatim in BigQuery for traceability.

---

## 4. Metric extraction strategy

### 4A. Metrics ingested directly from CLK (trusted, no recomputation)

Per-step rows and GNRL (per-cycle summary) rows in CLK already contain:


| CLK Column | Metric                                                   | Unit |
| ---------- | -------------------------------------------------------- | ---- |
| `Q,Ah`     | Capacity (charge/discharge)                              | Ah   |
| `E,Wh`     | Energy (charge/discharge)                                | Wh   |
| `C,F`      | Capacitance (from DCHCC 10-90% method per manual Sec 14) | F    |
| `ERc,mR`   | ESR at charge step change                                | mOhm |
| `ERd,mR`   | ESR at discharge step change                             | mOhm |
| `ESR,mR`   | ESR from periodic current interruption                   | mOhm |
| `EFq,%`    | Coulombic efficiency                                     | %    |
| `EFe,%`    | Energy efficiency                                        | %    |
| `Ilk,A`    | Leakage current                                          | A    |
| `Drt,s`    | Step/cycle duration                                      | s    |


These are the **primary source of truth** for all protocols.

### 4B. ESR recomputation from RAW (protocol-specific)

**Implemented (12 May 2026):** Boundary-based ESR in `[analysis/esr.py](../Tool/geyser_tool/analysis/esr.py)`. Eligible protocols: `STANDARD_CELL`, `BLOCK_CYCLING`, `CYCLABILITY`. CLK program must mention a 1 s rest (`Rest 1s`, `Rest during 1s`, or patterns in `program_has_rest_1s`). For each **DCC / DCCC / DCHCC** RAW segment immediately followed by **RLX / RLAX**; `v_end_v` and |I| from the **last** discharge row; `delay_s` ≈ **0.01** uses **first** RLX voltage; `delay_s` = **1.0** uses **last** RLX voltage. Multiple pairs per RAW file when the program repeats. See [README.md](../Tool/README.md) § `esr_computed`.

~~The following bullet list described an older current-threshold + `t₀` + nearest-sample method; it is **not** what the code does today. Kept as archival context only.~~

1. ~~Find the discharge-to-rest transition: the first sample where `|I| <= 0.02 * I_discharge`. Define this as `t=0`.~~
2. ~~`VEND` = last voltage sample where `|I| >= 0.98 * I_discharge`.~~
3. ~~`V10ms` / `V1s` from nearest samples at `t+10ms`, `t+1s` after rest start~~

**Legacy `esr.strict_mode` / `tolerance_s`** no longer affect boundary ESR; `delays_s` still selects which delay columns to emit.

**Block-style discharge ESR:** When block programs match the same protocol + program-text gates, the same boundary logic applies per RAW segment (not “average of last 5 cycles” in code unless added later).

### ~~4C. Protocol-specific metric rules (dispatch table)~~ — **LEGACY / NOT IMPLEMENTED**

> **Withdrawn.** There is **no** `PROTOCOL_METRICS` dictionary in the codebase. Per-protocol routing for ESR and curves was **never** centralized this way — see **`analysis/esr.py`**, **`upload/bigquery.py`**, **`analysis/curves.py`**. ~~A long illustrative Python sketch lived here in older plan revisions~~ — **omitted** (never landed in repo).

~~Archived `PROTOCOL_METRICS` Python sketch removed from this plan (never existed in repo).~~ Use **`analysis/esr.py`**, **`upload/bigquery.py`**, **`analysis/curves.py`** for routing truth.

### 4D. RAW time-series storage (all RAW files)

For **all** RAW files associated with each CLK run, the tool stores the full time-series in BigQuery:

- **Per-sample data**: time, voltage, current, temperature, capacity (Ah), energy (Wh)
- **Per-step context**: `step_no`, `step_type` (e.g. CCC, DCC, SNU) for each sample
- **Time concatenation**: In RAW files, `Time,s` resets to zero at each step. The tool **concatenates time** so that `time_s` is monotonically increasing from program start to end:
  - Within each RAW file: add cumulative offset per step (sum of previous step durations)
  - Across multiple RAW files (per cycle): add cycle-based offset so the full program timeline is continuous

This enables graph building (V–t, I–t, Ah–t, etc.) over the entire test program.

---

## 5. Data model (BigQuery tables)

### 5A. `test_runs` -- one row per file processed


| Column              | Type      | Description                                                   |
| ------------------- | --------- | ------------------------------------------------------------- |
| `run_id`            | STRING    | SHA256 hash of file path + size + mtime                       |
| `index_id`          | STRING    | e.g., `260203_G01_033`                                        |
| `object_id`         | STRING    | Full Object field from header, e.g., `260203_1_G01_033_C_001` |
| `entity_type`       | STRING    | `cell` or `block`                                             |
| `protocol_detected` | STRING    | e.g., `STANDARD_CELL`                                         |
| `program_text`      | STRING    | Verbatim program header                                       |
| `analyzer_model`    | STRING    | e.g., `ACK75.10.20.12`                                        |
| `channel`           | INT64     | Channel number                                                |
| `sample_rate_hz`    | FLOAT64   | Detected from RAW                                             |
| `test_started`      | TIMESTAMP | From header                                                   |
| `source_path`       | STRING    | Local file path                                               |
| `processed_at`      | TIMESTAMP | When ingested                                                 |


### 5B. `cycle_metrics` -- one row per cycle per file (from CLK GNRL lines)


| Column              | Type    |
| ------------------- | ------- |
| `run_id`            | STRING  |
| `index_id`          | STRING  |
| `cycle_no`          | INT64   |
| `duration_s`        | FLOAT64 |
| `voltage_end_v`     | FLOAT64 |
| `current_end_a`     | FLOAT64 |
| `temperature_c`     | FLOAT64 |
| `esr_periodic_ohm`  | FLOAT64 |
| `capacity_ah`       | FLOAT64 |
| `energy_wh`         | FLOAT64 |
| `capacitance_f`     | FLOAT64 |
| `esr_charge_ohm`    | FLOAT64 |
| `esr_discharge_ohm` | FLOAT64 |
| `leakage_current_a` | FLOAT64 |
| `coulombic_eff_pct` | FLOAT64 |
| `energy_eff_pct`    | FLOAT64 |


### 5C. `step_metrics` -- one row per step per cycle (from CLK per-step lines)

Same structure as `cycle_metrics` but with added `step_no`, `step_tag` (e.g., `5CCC`, `6DCC`), and `step_type` (e.g., `CCC`, `DCC`, `SNU`, `CCV`).

### 5D. `esr_computed` -- recomputed ESR from RAW (when applicable)


| Column           | Type    |
| ---------------- | ------- |
| `run_id`         | STRING  |
| `index_id`       | STRING  |
| `cycle_no`       | INT64   |
| `step_no`        | INT64   |
| `direction`      | STRING  |
| `delay_s`        | FLOAT64 |
| `esr_ohm`        | FLOAT64 |
| `v_end_v`        | FLOAT64 |
| `v_at_delay_v`   | FLOAT64 |
| `current_a`      | FLOAT64 |
| `sample_rate_hz` | FLOAT64 |
| `is_approximate` | BOOL    |
| `reason`         | STRING  |


### 5E. `time_series` -- full RAW time-series per sample


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


### 5F. `curves` -- downsampled curve points for Looker


| Column       | Type    |
| ------------ | ------- |
| `run_id`     | STRING  |
| `index_id`   | STRING  |
| `cycle_no`   | INT64   |
| `step_no`    | INT64   |
| `curve_type` | STRING  |
| `seq`        | INT64   |
| `x`          | FLOAT64 |
| `y`          | FLOAT64 |
| `y2`         | FLOAT64 |


### 5G. Deduplication and run_fingerprints

`run_id` is computed from file identity (path + size + mtime). On each upload, existing rows for the same `run_id` are replaced (delete+insert). `run_fingerprints` table stores per-table fingerprints (run_id, table_name, fingerprint, updated_at) for smart skip.

### 5H. Per-table fingerprint (smart skip)

**Implemented**: Per-table fingerprints from file metadata (path + size + mtime for CLK; CLK + RAW paths for time_series/esr/curves; config for esr/curves). Stored in `run_fingerprints` table. Before processing each file: compute fingerprints, query `run_fingerprints`; if all match, skip. Use `--force` or `--force-run RUN_ID` to override. After upload, upsert fingerprints.

**Duplicate prevention**: Skipped files = no delete, no insert. Only processed files use delete-by-run_id + insert. No duplicates.

### 5J. Parquet-GCS upload

**Implemented**: When `gcs.gcs_bucket` is set in config: Process → write parquet locally → upload parquet to GCS → BigQuery `load_table_from_uri` from GCS. Memory-efficient; retry from GCS on failure. When bucket not set, fall back to in-memory `load_table_from_dataframe`.

### 5K. Split stage and upload flow

**Implemented**: Two-step flow decouples fast local staging from the slower GCS upload bottleneck.

- **Stage to Parquet only**: Process files, write Parquet + `run_ids.txt` + `run_fingerprints.json` to a batch folder (e.g. `D:\Electrical_tests\_staging\batch_YYYYMMDD_HHMMSS\`). No GCS or BigQuery upload.
- **Upload batch**: Select a batch folder; upload its Parquet files to GCS, load into BigQuery, upsert fingerprints from `run_fingerprints.json`. Uses `run_ids.txt` for delete/load; fingerprints stored in batch for later upload.
- **GUI**: "Stage to Parquet" and "Upload batch…" buttons. "Update BigQuery" remains the all-in-one flow.
- **CLI**: `--stage-only` (with `--upload`) for stage only; `--upload-batch PATH` to upload a selected batch.

### 5I. Future: Dynamic max_points_per_run

**Problem**: Fixed `max_points_per_run` in config causes downsampling for high-rate (100 Hz) or long tests. At 100 Hz, 24 h = 8.6M points; at 10 Hz, 24 h = 864k points. A fixed 50k cap loses resolution.

**Proposed solution**: Read "Data recording period" from CLK/RAW header (already parsed as `data_period_s` in [parsers/header.py](Tool/geyser_tool/parsers/header.py)). Compute sample rate = 1 / data_period_s (e.g. 0.01 s -> 100 Hz). Set max_points per run dynamically:

- Option A: `max_points = sample_rate_hz * max_test_duration_s` (config: max_test_duration_hours)
- Option B: Rate-based lookup table (10 Hz -> 10M, 100 Hz -> 50M)
- Option C: No cap when rate is known; stream/upload all points (with performance safeguards)

**Key file**: [geyser_tool/upload/bigquery.py](Tool/geyser_tool/upload/bigquery.py) `build_time_series_rows` and [geyser_tool/config.py](Tool/geyser_tool/config.py) CurvesConfig. Header `data_period_s` is available via `parse_header(summary.path)` in `process_and_build_rows`.

---

## 6. Architecture

```
geyser_tool/
  __init__.py
  config.py                # YAML config load/save + defaults
  gui/
    app.py                 # Tkinter: file/folder picker, settings, progress, "Update BigQuery"
  cli/
    main.py                # argparse CLI mirror
  parsers/
    __init__.py
    header.py              # Parse metadata header: analyzer, object, program steps, limitations, sample rate
    clk_reader.py          # Parse CLK tabular data into cycle_metrics + step_metrics DataFrames
    raw_reader.py          # PARSE RAW; time_continuous_s, step_no, step_type
  protocol/
    __init__.py
    program_model.py       # ProgramStep, ProgramDefinition dataclasses
    detector.py            # Match parsed program against registry patterns
    registry.yaml          # Protocol pattern definitions (editable)
  analysis/
    __init__.py
    esr.py                 # Boundary-based ESR (discharge → RLX) for gated protocols
    curves.py              # CV, CYCLING downsampling from time_series (no dQ/dV)
  upload/
    __init__.py
    bigquery.py            # BigQuery client: create dataset/tables, upsert rows
    fingerprint.py        # Per-table fingerprint from file metadata (smart skip)
    auth.py                # Google Cloud service account auth
    staging.py             # Local Parquet cache; upload_staging_to_gcs for GCS path
  ids.py                   # Object ID parsing, index_id extraction, entity_type inference
  utils/
    __init__.py
    logging.py
    errors.py
  # tests/ — not a committed suite (pytest out of scope; see § Permanently out of scope)
scripts/
  build_exe.bat            # PyInstaller one-file
  setup_bigquery.py        # One-time: create dataset + tables in BigQuery
  migrate_add_run_fingerprints.py  # Add run_fingerprints table
config.yaml                # Default config
README.md
requirements.txt
```

---

## 7. Phased implementation

### Phase 1 -- MVP (core pipeline + BigQuery + basic dashboards)

**Goal**: End-to-end: pick files in GUI -> parse CLK -> detect protocol -> ingest metrics -> upload to BigQuery -> see data in Looker Studio.

- Header parser (metadata, program steps, Object ID)
- CLK reader (parse tabular metrics for all step and GNRL rows)
- Protocol detection engine + initial registry (Standard Cell, block presets, Cyclability, Unknown); **current** labels: §3.0 / `Tool/README.md`
- ID parsing and entity type inference from path + Object field
- BigQuery schema creation script + upload logic (with dedup)
- Google Cloud auth (service account JSON key)
- Local Parquet staging (write before upload; retry on failure)
- GUI: folder picker, progress bar, "Update BigQuery" button, log viewer
- CLI mirror
- Looker Studio: basic dashboards (ESR trends, capacity/energy per batch, efficiency tracking)
- ~~Unit tests for parser, protocol detection, ID parsing~~ — **not in scope** (see § Permanently out of scope)
- README + CONFIG docs

### Phase 2 -- ESR + curves (*mostly shipped; narrative below is archival*)

**Goal (original spec):** ESR from RAW; curve data; protocol coverage UI.

**Implemented in repo:** RAW reader + full `time_series`; **boundary-based** ESR and `esr_computed`; `**curves`** (CYCLING + CV, per-step downsampling); **no dQ/dV**. Looker/README document ESR semantics. Optional GUI cycle picker / formal DQ report were never required for MVP — not in scope unless re-prioritized.

- ~~Curve generation … dQ/dV~~ — **not in scope** (see § Permanently out of scope).

### Phase 3 -- ~~DCIR~~, ~~explorer~~, EXE (*superseded plan text*)

**Original stretch goals** included DCIR, registry explorer, extra YAML patterns, advanced Looker KPIs — all ~~**not in scope**~~ per § Permanently out of scope. **PyInstaller** remains optional deferred.

### Remaining work (authoritative short list)

- **Optional:** PyInstaller EXE + `build_exe.bat` for lab convenience
- **Optional:** tighten `time_series` ingest policy if warehouse cost dominates
- **Ongoing:** README / LOOKER_SETUP / this plan when behaviour changes

The following original “Phase 3 remaining” bullets are **closed** — do not surface as backlog:

- ~~DCIR extraction for pulse protocols~~
- ~~Advanced Looker dashboards (control charts, drift monitors, KPIs)~~ *(team may build ad hoc in Looker; not a tool milestone)*
- ~~Protocol registry explorer~~
- ~~Additional protocol patterns in YAML (Reference GCD, …)~~

**Implemented from older Phase 3 technical tracks:**

- **Per-table fingerprint**: Implemented. Fingerprint from file metadata; skip when all match; `--force` / `--force-run` override. `run_fingerprints` table stores per-table fingerprints.
- **Parquet-GCS upload**: Implemented. When `gcs.gcs_bucket` set: stage to parquet, upload to GCS, BigQuery loads from GCS. Memory-efficient; fallback to in-memory when bucket not set.
- **Split stage and upload**: Implemented. Stage to Parquet only (no GCS/Big); then Upload batch (select folder → GCS → BigQuery). Batches include `run_fingerprints.json` for fingerprint upsert on deferred upload.
- ~~**Dynamic max_points_per_run** (derive from analyzer **Data recording period** in header so 100 Hz / 10 Hz never get over-downsampled)~~ — **not implemented.** Curve thinning is **`curves.*`** in `config.yaml`; full-rate data remains in **`time_series`**. Optional warehouse-only tuning is **`phase3-dynamic-max-points`** in frontmatter (*cost*, not smarter sampling).

- ~~Unit tests~~ — **Permanently out of scope** (see § Permanently out of scope).

---

## 8. Key technology choices


| Concern           | Choice                                  | Rationale                                                    |
| ----------------- | --------------------------------------- | ------------------------------------------------------------ |
| GUI               | Tkinter                                 | Simple, Windows-native, stdlib                               |
| Data processing   | pandas + numpy                          | Standard, well-tested for tabular data                       |
| Local staging     | Parquet (pyarrow)                       | Efficient columnar format, BigQuery-compatible               |
| Cloud DB          | BigQuery                                | Your Google Suite requirement; scales; Looker-native         |
| Dashboards        | Looker Studio                           | Your Google Suite requirement; connects to BigQuery natively |
| Auth              | google-cloud-bigquery + service account | Standard GCP auth; no user login needed                      |
| Config            | YAML                                    | Human-readable, editable                                     |
| Packaging         | PyInstaller                             | One-file EXE for Windows                                     |
| Protocol registry | YAML                                    | Easy to add new protocols without code changes               |


---

## 9. Config defaults

**Canonical example:** mirror [`Tool/config.example.yaml`](../Tool/config.example.yaml). ~~Earlier plan drafts used flat keys (`output_staging_dir`, `bigquery_project`, `esr_recompute_enabled`, `curve_max_points`, `tags_override_heuristics`, …); those **never** matched [`AppConfig`](../Tool/geyser_tool/config.py).~~

```yaml
# Current shape — see Tool/config.example.yaml for comments and full keys.

paths:
  cells_root: D:\Electrical_tests\Cells
  blocks_root: D:\Electrical_tests\Blocks
  staging_root: D:\Electrical_tests\_staging

bq:
  project: geyser-testing-department
  dataset: electrical_tests
  service_account_key: null  # optional; ADC if null

gcs:
  gcs_bucket: ""
  gcs_staging_prefix: staging/

esr:
  delays_s: [0.010, 1.0]
  strict_mode: true   # boundary ESR ignores; kept for compat
  tolerance_s: 0.0

raw_ts:
  inter_cycle_gap_s: 0.0   # synthetic gap between stitched RAW files on time_s

curves:
  max_points_per_run: 50000   # legacy / whole-run cap when not segmenting
  curves_per_step_segment: true
  max_points_per_cycling_segment: 100
  max_points_per_cv_segment: 500
  # ... see config.example.yaml
```

---

## 10. Scope reductions from original XML spec

**Also authoritative:** the § **Permanently out of scope** table near the top — pytest, explorer UI, DCIR, dQ/dV, extra registry labels are **closed** (not deferred).


| Original feature                                    | Status in revised plan               | Reason                                                                 |
| --------------------------------------------------- | ------------------------------------ | ---------------------------------------------------------------------- |
| DuckDB (week + master)                              | **Removed**                          | Replaced by BigQuery as canonical DB per your Google Suite requirement |
| Streamlit dashboard                                 | **Removed**                          | Replaced by Looker Studio                                              |
| Local weekly Parquet partitions as DB               | **Simplified** to staging cache only | BigQuery is the master; local Parquet is just a upload buffer          |
| ~~Heuristic step detection~~ (fallback)               | **LEGACY — not planned**             | ~~Deferred~~ superseded; no standalone fallback detector on roadmap — header + **`Step`** column drive parsing |
| ~~dQ/dV recomputation from I*dt~~                   | **Not in scope**                     | See § Permanently out of scope                                         |
| ~~Automated pytest~~                                | **Not in scope**                     | See § Permanently out of scope                                         |
| ~~Protocol explorer UI~~                            | **Not in scope**                     | See § Permanently out of scope                                         |
| ~~DCIR extractor~~                                  | **Not in scope**                     | See § Permanently out of scope                                         |
| ~~Extra registry labels~~ (Reference GCD, FCR-D, …) | **Not in scope**                     | See § Permanently out of scope                                         |
| CONTRIBUTING.md, ARCHITECTURE.md                    | **Simplified**                       | README + CONFIG docs are sufficient for MVP                            |
| PyInstaller EXE                                     | **Optional / deferred**              | Run as Python script; lab convenience only                             |


---

## 11. Implementation Changelog

### RAW ETL fidelity and Looker-aligned curves — 2026-05 (active focus)

- `**raw_ts.inter_cycle_gap_s`:** Synthetic gap between concatenated RAW cycle files on `**time_series.time_s`**; **default `0`** (`[config.py](Tool/geyser_tool/config.py)`, `[build_time_series_rows](Tool/geyser_tool/upload/bigquery.py)`). Set a positive value (e.g. `0.01`) only if you want an explicit delimiter.
- **RAW documentation:** `[raw_reader](Tool/geyser_tool/parsers/raw_reader.py)` module docs describe `**Time,s`**, `**Step**` segmentation, `**time_continuous_s**`, and relationship to per-step durations.
- `**curves`:** Default **per `(cycle_no, step_no)`** downsampling with edge preservation; CV caps tighten when CLK `**sample_rate_hz**` is low — see `[curves.py](Tool/geyser_tool/analysis/curves.py)` and `[LOOKER_SETUP.md](Tool/docs/LOOKER_SETUP.md)`.
- **Operational focus:** RAW ingestion accuracy, dashboard-friendly `**curves`**, Looker documentation. Items permanently excluded from backlog: § **Permanently out of scope** (pytest, explorer, DCIR, dQ/dV, extra registry labels).

### ESR boundary recomputation — 2026-05/06

- `**esr_computed`:** Discharge markers **DCC / DCCC / DCHCC** followed by **RLX / RLAX**; `**v_end_v`** / **|I|** from last discharge row; `**delay_s`** 0.01 → first RLX V; `**delay_s**` 1.0 → last RLX V. Gated by `**STANDARD_CELL**`, `**BLOCK_CYCLING**`, `**CYCLABILITY**` and program phrases `**Rest 1s**`, `**Rest during 1s**` (see `[esr.py](Tool/geyser_tool/analysis/esr.py)`, `[bigquery.py](Tool/geyser_tool/upload/bigquery.py)`).
- **Supersedes** prior current-threshold + strict sample-window ESR for these gates.

### Protocol labels & CYCLABILITY gating — 2026-05

- `**VTT_TEST1_BLOCK`** (and similarly named placeholders) retired from dashboards and ingestion; `**test_runs.protocol_detected`** now uses `**BLOCK_SCANNING**`, `**BLOCK_CYCLING**`, `**CYCLABILITY**`, `**STANDARD_CELL**`, `**UNKNOWN**`.
- `**CYCLABILITY**` is evaluated **before** `**registry.yaml`**: numeric gates `**> 20`** cycles; cells vs blocks separated by `**Discharge CC**` (correct spelling) vs CLK typo `**Discarge CC**`. See `**Tool/geyser_tool/protocol/detector.py**` and `**Tool/README.md**` § Protocol detection.
- `**Tool/docs/LOOKER_SETUP.md**` filter docs updated (`protocol_detected` list).
- **BigQuery `channel` staging**: typed as nullable **INT64** in Parquet where applicable to satisfy load jobs (see staging column rules in `**upload/staging.py`**—ongoing schema hygiene).

### Phase 1 & 2 completed (as of 2026-03)

**Parsers**

- Header parser ([parsers/header.py](Tool/geyser_tool/parsers/header.py)): metadata, program steps, Object ID
- CLK reader ([parsers/clk_reader.py](Tool/geyser_tool/parsers/clk_reader.py)): cycle + step metrics, empty-step crash fix
- RAW reader ([parsers/raw_reader.py](Tool/geyser_tool/parsers/raw_reader.py)): time concatenation, footer exclusion

**RAW parser updates (Blocks + Cells)**

- **Blocks format**: `RAW/` subfolder, e.g. `.../001/RAW/{object_id}-00000000.txt` (ACK75.48)
- **Cells format**: `{object_id}-RAW/` folder, e.g. `.../001/{object_id}-RAW/{object_id}-00000001.txt` (ACK75.10)
- **Footer fix**: Exclude lines like `Aborted by user: 11/02/2026` from table parse to avoid date strings in Time,s column
- Column aliases support both analyzers (ESR,mR, ESR,Ohm, T,°C, etc.)

**BigQuery schema extensions**

- `test_runs`: `date_ymd`, `group_id`, `test_date` (from [ids.py](Tool/geyser_tool/ids.py))
- `cycle_metrics`: `capacity_charge_ah`, `capacity_discharge_ah`, `energy_charge_wh`, `energy_discharge_wh` (aggregated from step_metrics)
- Migration scripts: `migrate_add_date_group.py`, `migrate_add_charge_discharge.py`

**Config**

- Default project: `geyser-testing-department` (aligned across config.py, setup_bigquery.py, LOOKER_SETUP.md)

**GUI**

- Implemented with Tkinter (not PySimpleGUI): folder picker, Scan, Save summary, Update BigQuery

### Phase 3: Parquet-GCS and per-table fingerprint (as of 2026-03)

**Parquet-GCS upload**

- GCSConfig (`gcs_bucket`, `gcs_staging_prefix`) in [config.py](Tool/geyser_tool/config.py)
- `upload_staging_to_gcs()` in [staging.py](Tool/geyser_tool/upload/staging.py): upload parquet to GCS
- BigQuery `load_table_from_uri` when `gcs_bucket` set; fallback to in-memory when not set
- `google-cloud-storage` dependency

**Per-table fingerprint**

- [fingerprint.py](Tool/geyser_tool/upload/fingerprint.py): `compute_fingerprints()` from file metadata (no content read)
- `run_fingerprints` table: run_id, table_name, fingerprint, updated_at
- Smart skip: if all fingerprints match, skip processing and upload
- `--force` and `--force-run RUN_ID` CLI flags to override
- Migration: `migrate_add_run_fingerprints.py`

### Phase 3: Split stage and upload flow (as of 2026-03)

**Two-step flow**

- **Stage to Parquet only**: `stage_only()` in [bigquery.py](Tool/geyser_tool/upload/bigquery.py) processes files, calls `stage_to_parquet()` with `uploaded_summaries` to write Parquet + `run_ids.txt` + `run_fingerprints.json` to batch folder. Returns batch path and staged count.
- **Upload batch**: `upload_batch_to_bigquery(batch_dir)` reads `run_ids.txt` and `run_fingerprints.json`, deletes existing BigQuery rows, uploads Parquet to GCS (or loads from local Parquet if GCS disabled), loads into BigQuery, upserts fingerprints.

**Batch folder contents**

- `test_runs.parquet`, `cycle_metrics.parquet`, `step_metrics.parquet`, `time_series.parquet`, `esr_computed.parquet`, `curves.parquet`
- `run_ids.txt` (one run_id per line)
- `run_fingerprints.json` (run_id, table_name, fingerprint, updated_at per row)

**GUI** ([app.py](Tool/geyser_tool/gui/app.py))

- "Stage to Parquet" button: calls `stage_only()`, logs staged path
- "Upload batch…" button: folder picker (initialdir=staging_root), calls `upload_batch_to_bigquery()`

**CLI** ([main.py](Tool/geyser_tool/cli/main.py))

- `--stage-only` (with `--upload`): process and stage only
- `--upload-batch PATH`: upload a batch folder (paths argument optional)

