# Geyser Cycler Analysis Tool

Protocol-aware Python tool for ingesting CLK/RAW cycler logs from ASK/ACK75 analyzers, detecting protocols, extracting metrics, and uploading to BigQuery for Looker Studio dashboards.

This README is the **primary user-facing** description of the tool. The engineering plan (`../.cursor/plans/geyser_cycler_analysis_tool_8562c610.plan.md`) tracks historical decisions, backlog, and should be updated alongside code changes.

## Functional overview (current iteration)

### What runs when you use the tool

1. **Discovery** — The CLI or GUI resolves one or more paths to `*-CLK.txt` files (explicit file paths or recursive directory scan).

2. **Header and program parsing** — For each CLK, the tool reads the analyzer header (`Object:`, analyzer model, optional data period, verbatim `Program:` lines). Program lines become a flat list of **`ProgramStep.mode`** strings (text after the step number).

3. **Protocol detection** — Modes are uppercased and joined; **`detect_protocol()`** assigns `protocol_detected` (stored in **`test_runs`** as `protocol_detected`). See **[Protocol detection](#protocol-detection)** below.

4. **Identity fields** — From the **`Object:`** header and file path pattern, **`index_id`**, **`entity_type`** (**`cell`** / **`block`**), **`date_ymd`**, **`group_id`**, **`test_date`** are derived (`geyser_tool/ids.py`). **Important:** **`entity_type`** is independent of **`protocol_detected`**—protocol is inferred from **program text**, not from whether the specimen is labeled a cell or block in IDs.

5. **CLK metrics** — Tabular **`GNRL`** (per-cycle) and per-step metrics are parsed from the CLK (`geyser_tool/parsers/clk_reader.py`) for **`cycle_metrics`**, **`step_metrics`**. Charge/discharge capacity and energy are aggregated where applicable.

6. **RAW (optional)** — Unless **`--no-raw`** / GUI equivalent, sibling RAW files are discovered, parsed into **`time_series`** (**all** samples — no downsampling). **`curves`** holds **downsampled** points for Looker-friendly charts; **`esr_computed`** uses the full per-file RAW trajectory. See **[RAW time axis and BigQuery `time_s`](#raw-time-axis-and-bigquery-ts)**.

7. **Fingerprints** — Before heavy work, per-table fingerprints (from file paths, sizes, mtimes—not full content) are compared with **`run_fingerprints`** in BigQuery. **Matched** fingerprints **skip** reprocessing unless **`--force`** or **`--force-run RUN_ID`**.

8. **Output path** —
   - **Scan only:** one summary line per CLK (no BigQuery writes).
   - **`--upload`:** process eligible files → stage Parquet internally → optionally upload to **GCS** (if **`gcs.gcs_bucket`** set) → **load** tables in BigQuery; upsert fingerprints.
   - **`--upload --stage-only`:** same processing but stops after writing a **`batch_YYYYMMDD_HHMMSS`** folder under **`paths.staging_root`** (Parquet tables + **`run_ids.txt`** + **`run_fingerprints.json`**). No GCS/BigQuery unless you later run **`--upload-batch`**.
   - **`--upload-batch PATH`:** load an existing batch folder to GCS (if configured) + BigQuery; uses **`run_ids.txt`** for delete-by-run semantics before load.

### GUI (`python -m geyser_tool.gui.app`)

Mirrors the CLI ideas: browse a folder to list CLK headers, optionally **stage** to Parquet (**Stage to Parquet**), upload a staged batch (**Upload batch…**), or run end-to-end **Update BigQuery**. **Force re-upload** aligns with **`--force`**.

### Package layout (where to read code)

| Area | Module(s) |
|------|-----------|
| Config | `geyser_tool/config.py` |
| CLK / RAW parsers | `geyser_tool/parsers/` |
| Pipeline entry + summary | `geyser_tool/pipeline.py` |
| Protocols | `geyser_tool/protocol/detector.py`, `registry.yaml` |
| IDs and dates | `geyser_tool/ids.py` |
| Analysis (ESR, curves) | `geyser_tool/analysis/` |
| BigQuery staging & load | `geyser_tool/upload/bigquery.py`, `staging.py`, `fingerprint.py` |

### Related documentation

| Document | Purpose |
|----------|---------|
| [docs/LOOKER_SETUP.md](docs/LOOKER_SETUP.md) | Dimensions, charts, **`protocol_detected`** filters |
| [.cursor/plans/geyser_cycler_analysis_tool_8562c610.plan.md](../.cursor/plans/geyser_cycler_analysis_tool_8562c610.plan.md) | Phased plan, changelog, backlog |
| `config.example.yaml` | GCP, staging, RAW stitch, and curve sampling knobs |

## RAW time axis and BigQuery `time_s`

- **Within one RAW file:** The analyzer logs **`Time,s`** from **0** at each **new `Step`** row (e.g. `6DCCC`, `7RLAX`). The reader builds **`time_continuous_s`** by detecting **contiguous runs of identical `Step`** and advancing an offset by **`max(Time,s)`** in each run — so per-step **durations match the RAW table** (e.g. 52.4 s CC remains 52.4 s on the within-file axis). See [`geyser_tool/parsers/raw_reader.py`](geyser_tool/parsers/raw_reader.py).
- **BigQuery `time_series.time_s`:** Uses **`time_continuous_s`** plus a cumulative offset when multiple RAW files (cycles) are stitched for one CLK. Optional synthetic gap between files: **`raw_ts.inter_cycle_gap_s`** in `config.yaml` (default **`0`** — no gap; set e.g. **`0.01`** if you want a small delimiter). See [`build_time_series_rows`](geyser_tool/upload/bigquery.py).
- **Dashboards vs warehouse:** **`time_series`** is full fidelity for accuracy and ESR math. **`curves`** is **pre-thinned** (per `(cycle_no, step_no)` by default) so Looker line/scatter charts stay within typical **~5k–10k** point UI limits — tune under **`curves:`** in config.

## Quick Start

```powershell
# Install dependencies
pip install -r requirements.txt

# Copy and edit config (project ID, paths, service account key)
copy config.example.yaml config.yaml

# Create BigQuery tables (one-time)
python -m scripts.setup_bigquery --project geyser-testing-department --key your-key.json

# If test_runs already exists, add date_ymd/group_id/test_date columns:
python -m scripts.migrate_add_date_group --project geyser-testing-department --key your-key.json

# If cycle_metrics already exists, add capacity_charge/discharge and energy_charge/discharge columns:
python -m scripts.migrate_add_charge_discharge --project geyser-testing-department --key your-key.json

# Upload data
python -m geyser_tool.cli.main "D:\Electrical_tests\Cells\2026" --upload
```

## Config

`config.yaml` in the Tool directory is auto-loaded. Key settings:

- **bq.project** – GCP project ID
- **bq.dataset** – BigQuery dataset (default: electrical_tests)
- **bq.service_account_key** – Path to JSON key (use forward slashes)
- **paths.staging_root** – Folder where `--stage-only` writes `batch_YYYYMMDD_HHMMSS/` folders
- **gcs.gcs_bucket**, **gcs.gcs_staging_prefix** – If set, `upload-batch` uploads Parquets here before loading BigQuery (`gs://<bucket>/<prefix>/<batch_name>/…`)
- **raw_ts.inter_cycle_gap_s** – Extra seconds between stitched RAW cycle files on the global `time_s` axis (default **`0`**; set e.g. **`0.01`** for a legacy-style delimiter).
- **curves.*** – Per-step curve downsampling for Looker (`curves_per_step_segment`, `max_points_per_cycling_segment`, `max_points_per_cv_segment`, etc.); see `config.example.yaml`.

## CLI

Run from the **Tool** directory (or ensure `PYTHONPATH` includes this folder).

Two entry modes:

1. **Scan / upload / stage** — pass one or more paths to CLK files or directories (`paths`; use spaces for multiple roots).
2. **Upload batch** — `--upload-batch PATH` uploads an existing staged folder; **paths are not used**.

See also: `python -m geyser_tool.cli.main --help`

### Arguments and flags

| Argument / flag | Required / when | Meaning |
|-----------------|-----------------|--------|
| **paths** | Unless `--upload-batch` | One or more CLK files or directories to scan recursively for `*-CLK.txt`. Example: `"D:\Cells\2025\09"` `"D:\Cells\2025\10"`. |
| `--config PATH` | No | YAML config path. If omitted, `config.yaml` in the Tool directory is used when present. |
| `--upload` | For upload flows | Enables processing aimed at BigQuery (`upload_processed_files`) or staging (`stage_only` with `--stage-only`). |
| `--stage-only` | With `--upload` | Process CLK/RAW and write Parquet under `staging_root` only; **no** GCS or BigQuery upload. Prints batch folder path when successful. |
| `--upload-batch PATH` | Standalone flow | Folder of a staged batch (`batch_YYYYMMDD_HHMMSS`): uploads Parquets to GCS (if configured) and loads tables in BigQuery. Does not read `paths`. |
| `--no-raw` | With `--upload` | CLK-only pipeline: skips RAW reading (no full `time_series` / RAW-derived curves / ESR from RAW). Faster, lower memory. |
| `--dry-run` | With `--upload` (**without** `--stage-only`) | Runs processing but skips the actual BigQuery/GCS upload. |
| `--force` | With `--upload` | Ignores fingerprint match; processes and uploads/stages everything in the scan. |
| `--force-run RUN_ID` | With `--upload` | Repeatable. Force processing/upload for specific `run_id` values while others may still fingerprint-skip unless `--force` is also set. |
| _(no `--upload`)_ | — | Prints a one-line summary per CLK file only (scan mode). |

### Example commands

```powershell
# Scan and print summaries (no upload)
python -m geyser_tool.cli.main "D:\Electrical_tests\Cells\2026"

# Upload to BigQuery (process + stage + load)
python -m geyser_tool.cli.main "D:\Electrical_tests\Cells\2026" --upload

# Stage only → local Parquet batch; upload later with --upload-batch
python -m geyser_tool.cli.main "D:\Electrical_tests\Cells\2025\09" --upload --stage-only --force

# Upload a staged batch folder to GCS + BigQuery
python -m geyser_tool.cli.main --upload-batch "D:\Electrical_tests\_staging\batch_20260323_102958"

# CLK only (skip RAW, faster, less memory)
python -m geyser_tool.cli.main "D:\Electrical_tests\Cells\2026" --upload --no-raw

# Dry run (process only, no upload) — not used together with --stage-only
python -m geyser_tool.cli.main "D:\Electrical_tests\Cells\2026" --upload --dry-run

# Custom config file
python -m geyser_tool.cli.main --config my-config.yaml "D:\Electrical_tests\Cells\2026" --upload --stage-only --force

# Multiple roots in one staging run (watch RAM — staging one folder at a time is safer)
python -m geyser_tool.cli.main --upload --stage-only --force "D:\Electrical_tests\Cells\2025\09" "D:\Electrical_tests\Cells\2025\10"
```

### Staged batches vs GCS and BigQuery

The tool **does not** maintain a ledger of “which local batch folders have been uploaded.” You infer it from GCP and BigQuery:

**GCS (if `gcs.gcs_bucket` is set)**

Objects land under:

`gs://<bucket>/<gcs_staging_prefix>/<batch_folder_name>/<table>.parquet`

Example: `gs://your-bucket/staging/batch_20260323_102958/test_runs.parquet`.  
In the Cloud Console, open Storage → bucket → prefix (`staging/` or your configured prefix) and look for folders named like your local `batch_*` directories.

**BigQuery**

Each staged batch folder includes **`run_ids.txt`** (one `run_id` per line). If an upload succeeds, rows for those `run_id` values appear in dataset tables (`test_runs`, `cycle_metrics`, etc.). Spot-check alignment, for example:

```sql
-- Paste quoted run IDs from run_ids.txt into UNNEST, or load run_ids.txt into a staging table first
SELECT run_id FROM `your-project.your-dataset.test_runs`
WHERE run_id IN UNNEST(['abcd12345678901234567890', '...'])
ORDER BY run_id;
```

If all `run_id` values from a batch folder exist with expected data **and** the same batch folder name appears under your GCS prefix, that batch was uploaded end-to-end.

**Re-running `--upload-batch`** on the same folder deletes rows for those `run_ids` in the target tables, then reloads Parquet from disk (fix local Parquets before re-uploading if a previous load partially failed).

## GUI

```powershell
python -m geyser_tool.gui.app
```

Controls:

- **Scan headers** – Populate the list from the chosen folder (`Browse…`), show counts in the log.
- **Save summary…** – Export the current scan summary to a text file.
- **Stage to Parquet** – Same idea as `--upload --stage-only` (uses **Force re-upload** checkbox for `--force`). Logs staged/skipped/failed counts and batch path.
- **Upload batch…** – Pick a `batch_*` folder under staging root → uploads to GCS (if configured) and loads BigQuery.
- **Update BigQuery** – Same idea as `--upload` (direct process + upload, not staged-only).
- **Force re-upload** – When checked: ignore fingerprint skips for staged/upload flows tied to scans.
- **Exit**

## BigQuery Tables

| Table | Description |
|-------|-------------|
| test_runs | Metadata per file |
| cycle_metrics | Per-cycle metrics (GNRL) |
| step_metrics | Per-step metrics |
| time_series | Raw time-series (time, V, I, T, Ah, Wh) |
| curves | Downsampled CYCLING/CV curves |
| esr_computed | ESR from RAW step boundaries for Standard Cell / Block cycling / Cyclability (see below) |

### `esr_computed` (boundary method)

ESR rows are emitted only when **`test_runs.protocol_detected`** is one of **`STANDARD_CELL`**, **`BLOCK_CYCLING`**, or **`CYCLABILITY`**, and the CLK **Program** text indicates a **1 s rest** — e.g. **`Rest 1s`**, **`Rest during 1s`**, or other matches from **`program_has_rest_1s`** in [`geyser_tool/analysis/esr.py`](geyser_tool/analysis/esr.py).

For **each** RAW segment whose step marker is **`DCC`**, **`DCCC`**, or **`DCHCC`** and whose **next** contiguous segment is **`RLX`** or **`RLAX`**:

- **`delay_s` ≈ 0.01** — **`v_at_delay_v`** is the **first** voltage sample of the RLX step; **`v_end_v`** is the **last** voltage of the discharge step; **`esr_ohm`** = (**`v_at_delay_v` − `v_end_v`**) / **`current_a`** (|I| from the last discharge row). The **`delay_s`** column label is unchanged; the value is the **first RLX sample**, not a time-interpolated 10 ms point.
- **`delay_s` = 1.0** — **`v_at_delay_v`** is the **last** voltage sample of that RLX step (end of ~1 s rest).

There is **one `step_no` per discharge segment** (multiple pairs per RAW file when the protocol repeats). Other protocols, or eligible labels without a matching rest phrase in the program, produce **no** `esr_computed` rows. **`esr.strict_mode`** / **`tolerance_s`** do not affect this path (delays still come from **`esr.delays_s`**).

## Looker Studio

See [docs/LOOKER_SETUP.md](docs/LOOKER_SETUP.md) for dashboard setup.

## Protocol detection

Detected labels are **`STANDARD_CELL`**, **`BLOCK_SCANNING`**, **`BLOCK_CYCLING`**, **`CYCLABILITY`**, **`UNKNOWN`** (BigQuery **`test_runs.protocol_detected`**).

### Matching order (deterministic)

1. **`CYCLABILITY`** — Evaluated **first** in **`detector.py`** (not in YAML), so high–cycle-count block programs are not mistaken for **`BLOCK_CYCLING`**.
   - **Cell-style:** substring **`Charge CC`**, **`Discharge CC`** (text uppercased to **`DISCHARGE CC`**), regexp **`Cycle to step <step> <count> times`** with **`<count> > 20`**, and the joined program text must **not** contain the block typo **`Discarge CC`** — which uppercases to **`DISCARGE CC`** (**no `H`** after `DISC`, unlike **`DISCHARGE CC`**).
   - **Block-style:** **`Charge CC`**, typo discharge **`Discarge CC`**, regexp **`Preset number of cycles: <N>`** with **`N > 20`** (strict **`>`**, so exactly **20** does **not** qualify).
   - Confidence for this branch is **`0.5`**.
2. **YAML registry** — Rows in **`geyser_tool/protocol/registry.yaml`** apply **in file order**. Each row supports **`keywords_all`** (AND), **`keywords_any`** (OR), optional **`min_steps`**, **`confidence`**.
3. **Fallback heuristics** — If YAML is missing/empty **or** no row matches (uses the same cues as **`STANDARD_CELL`** / **`BLOCK_*`** keywords).

Extend **`BLOCK_*`** patterns by editing **`registry.yaml`**. Extend **`CYCLABILITY`** thresholds or shapes by editing **`detector.py`** (regex-only rules do not belong in YAML without code support).

See [`docs/LOOKER_SETUP.md`](docs/LOOKER_SETUP.md) for **`protocol_detected`** in dashboards.
