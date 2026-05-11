"""
Local Parquet staging for offline resilience and retry on BigQuery failure.
GCS upload for memory-efficient BigQuery load.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from geyser_tool.config import AppConfig
from geyser_tool.upload.fingerprint import compute_fingerprints

_TABLE_NAMES = ("test_runs", "cycle_metrics", "step_metrics", "time_series", "esr_computed", "curves")

# Columns that must be FLOAT64 in Parquet (BigQuery rejects INT32 for these)
_FLOAT_COLUMNS: Dict[str, List[str]] = {
    "test_runs": ["sample_rate_hz"],
    "cycle_metrics": [
        "duration_s", "voltage_end_v", "current_end_a", "temperature_c",
        "esr_periodic_ohm", "capacity_ah", "energy_wh", "capacity_charge_ah",
        "capacity_discharge_ah", "energy_charge_wh", "energy_discharge_wh",
        "capacitance_f", "esr_charge_ohm", "esr_discharge_ohm", "leakage_current_a",
        "coulombic_eff_pct", "energy_eff_pct",
    ],
    "step_metrics": [
        "duration_s", "voltage_end_v", "current_end_a", "temperature_c",
        "esr_periodic_ohm", "capacity_ah", "energy_wh", "capacitance_f",
        "esr_charge_ohm", "esr_discharge_ohm", "leakage_current_a",
        "coulombic_eff_pct", "energy_eff_pct",
    ],
    "time_series": ["time_s", "voltage_v", "current_a", "temperature_c", "capacity_ah", "energy_wh"],
    "esr_computed": ["delay_s", "esr_ohm", "v_end_v", "v_at_delay_v", "current_a", "sample_rate_hz"],
    "curves": ["x", "y", "y2"],
}

# Columns that must be INT64 in Parquet (BigQuery rejects DOUBLE for these)
_INT64_COLUMNS: Dict[str, List[str]] = {
    "test_runs": ["channel"],
}


def upload_staging_to_gcs(
    local_batch_dir: Path,
    bucket: str,
    prefix: str,
    credentials: Any = None,
) -> Dict[str, str]:
    """
    Upload parquet files from local batch dir to GCS.
    Returns dict mapping table_name -> gcs_uri (e.g. gs://bucket/prefix/batch_xxx/test_runs.parquet).
    """
    try:
        from google.cloud import storage
    except ImportError:
        return {}

    client = storage.Client(credentials=credentials) if credentials else storage.Client()
    bucket_obj = client.bucket(bucket)
    batch_name = local_batch_dir.name
    gcs_prefix = prefix.rstrip("/") + "/" + batch_name
    result: Dict[str, str] = {}

    for name in _TABLE_NAMES:
        parquet_path = local_batch_dir / f"{name}.parquet"
        if parquet_path.exists():
            blob_name = f"{gcs_prefix}/{name}.parquet"
            blob = bucket_obj.blob(blob_name)
            blob.upload_from_filename(str(parquet_path), content_type="application/octet-stream")
            result[name] = f"gs://{bucket}/{blob_name}"

    return result


def stage_to_parquet(
    run_ids: List[str],
    test_runs: List[Dict[str, Any]],
    cycle_metrics: List[Dict[str, Any]],
    step_metrics: List[Dict[str, Any]],
    time_series: List[Dict[str, Any]],
    esr_computed: List[Dict[str, Any]],
    curves: List[Dict[str, Any]] | None = None,
    config: AppConfig | None = None,
    uploaded_summaries: Optional[List[Tuple[str, Any]]] = None,
) -> Path | None:
    """
    Write all data to Parquet files in staging_root for retry/offline use.
    Returns path to staging directory, or None if staging_root not set.
    """
    cfg = config or AppConfig.load()
    staging = Path(cfg.paths.staging_root)
    if not staging:
        return None
    staging.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_dir = staging / f"batch_{ts}"
    batch_dir.mkdir(parents=True, exist_ok=True)

    def write_df(name: str, rows: List[Dict[str, Any]]) -> None:
        if rows:
            df = pd.DataFrame(rows)
            for col in _INT64_COLUMNS.get(name, []):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
            table = pa.Table.from_pandas(df)
            for col in _FLOAT_COLUMNS.get(name, []):
                if col in table.column_names and table.schema.field(col).type != pa.float64():
                    col_idx = table.column_names.index(col)
                    table = table.set_column(col_idx, col, table.column(col).cast(pa.float64()))
            pq.write_table(table, batch_dir / f"{name}.parquet")

    write_df("test_runs", test_runs)
    write_df("cycle_metrics", cycle_metrics)
    write_df("step_metrics", step_metrics)
    write_df("time_series", time_series)
    write_df("esr_computed", esr_computed)
    write_df("curves", curves or [])
    (batch_dir / "run_ids.txt").write_text("\n".join(run_ids), encoding="utf-8")

    if uploaded_summaries:
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        fp_rows: List[Dict[str, Any]] = []
        for run_id, summary in uploaded_summaries:
            fps = compute_fingerprints(summary.path, config=cfg)
            for table_name, fp in fps.items():
                fp_rows.append({
                    "run_id": run_id,
                    "table_name": table_name,
                    "fingerprint": fp,
                    "updated_at": now,
                })
        (batch_dir / "run_fingerprints.json").write_text(
            json.dumps(fp_rows, indent=2, default=str), encoding="utf-8"
        )

    return batch_dir
