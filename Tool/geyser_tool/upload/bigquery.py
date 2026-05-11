"""
BigQuery schema, row builders, and upload logic for electrical test data.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

import pandas as pd

from geyser_tool.config import AppConfig
from geyser_tool.pipeline import FileSummary
from geyser_tool.parsers.clk_reader import read_clk_with_metrics
from geyser_tool.parsers.header import FileHeader, parse_header
from geyser_tool.parsers.raw_reader import read_all_raw_for_clk
from geyser_tool.analysis.esr import compute_esr_from_discharge_rest, ESRResult
from geyser_tool.upload.staging import _TABLE_NAMES, stage_to_parquet, upload_staging_to_gcs
from geyser_tool.upload.fingerprint import compute_fingerprints
from geyser_tool.analysis.curves import build_curves_rows


# Column name aliases: CLK files use different names across analyzers
# (ESR,mR vs ESRa,Ohm, T,°C vs Ta,°C). We map to canonical names.
_CLK_COL_ALIASES: Dict[str, List[str]] = {
    "duration_s": ["Drt,s", "Drt,s"],
    "voltage_v": ["Ue,V", "Ue,V"],
    "current_a": ["Ie,A", "Ie,A"],
    "temperature_c": ["T,°C", "Ta,°C", "T,C"],
    "esr_periodic": ["ESR,mR", "ESRa,Ohm"],
    "capacity_ah": ["Q,Ah", "Q,Ah"],
    "energy_wh": ["E,Wh", "E,Wh"],
    "capacitance_f": ["C,F", "C,F"],
    "esr_charge": ["ERc,mR", "ESRc,Ohm"],
    "esr_discharge": ["ERd,mR", "ESRd,Ohm"],
    "leakage_a": ["Ilk,A", "Ilk,A"],
    "coulombic_eff_pct": ["EFq,%", "EFq,%"],
    "energy_eff_pct": ["EFe,%", "EFe,%"],
}


def _find_col(df: pd.DataFrame, canonical: str) -> Optional[str]:
    """Find the first matching column in df for a canonical name."""
    aliases = _CLK_COL_ALIASES.get(canonical, [canonical])
    for a in aliases:
        for c in df.columns:
            if str(c).strip() == a:
                return c
    return None


def _get_val(row: pd.Series, df: pd.DataFrame, canonical: str) -> Optional[float]:
    """Get float value from row, handling mR vs Ohm for ESR columns."""
    col = _find_col(df, canonical)
    if col is None:
        return None
    val = row.get(col)
    if pd.isna(val):
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    # ESR in mR -> convert to Ohm (divide by 1000)
    if canonical in ("esr_periodic", "esr_charge", "esr_discharge"):
        col_str = str(col)
        if "mR" in col_str:
            v = v / 1000.0
    return v


def compute_run_id(path: Path) -> str:
    """Deterministic run_id from file path and stats (size, mtime)."""
    try:
        stat = path.stat()
        payload = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime}"
    except OSError:
        payload = str(path.resolve())
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _parse_channel(analyzer: str) -> Optional[int]:
    """Extract channel number from analyzer line, e.g. 'Channel: 4'."""
    m = re.search(r"Channel:\s*(\d+)", analyzer, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _parse_test_started(started_raw: Optional[str]) -> Optional[str]:
    """Parse 'Testing started: 27/02/2026 16:30:35' to ISO."""
    if not started_raw:
        return None
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})", started_raw)
    if not m:
        return None
    d, mo, y, h, mi, s = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
        return dt.isoformat()
    except ValueError:
        return None


def _date_ymd_to_iso(ymd: str | None) -> str | None:
    """Convert YYMMDD to YYYY-MM-DD for BigQuery DATE. Assumes 20YY for year."""
    if not ymd or len(ymd) != 6:
        return None
    try:
        yy, mm, dd = int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6])
        year = 2000 + yy if yy < 100 else yy
        return f"{year:04d}-{mm:02d}-{dd:02d}"
    except (ValueError, IndexError):
        return None


def _date_ymd_to_date(ymd: str | None) -> date | None:
    """Convert YYMMDD to datetime.date for BigQuery DATE column."""
    iso = _date_ymd_to_iso(ymd)
    if not iso:
        return None
    try:
        return datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return None


def build_test_runs_rows(
    summary: FileSummary,
    run_id: str,
    header: Optional[FileHeader] = None,
) -> Dict[str, Any]:
    """Build a single test_runs row."""
    hdr = header or parse_header(summary.path)
    program_text = "\n".join(hdr.program_lines) if hdr.program_lines else None
    sample_rate_hz = 1.0 / hdr.data_period_s if hdr.data_period_s and hdr.data_period_s > 0 else None
    test_date = _date_ymd_to_date(summary.date_ymd)
    return {
        "run_id": run_id,
        "index_id": summary.index_id,
        "object_id": summary.object_id,
        "entity_type": summary.entity_type,
        "protocol_detected": summary.protocol.label,
        "program_text": program_text,
        "analyzer_model": hdr.analyzer or None,
        "channel": _parse_channel(hdr.analyzer or ""),
        "sample_rate_hz": sample_rate_hz,
        "test_started": _parse_test_started(hdr.started_raw),
        "date_ymd": summary.date_ymd,
        "group_id": summary.group_id,
        "test_date": test_date,
        "source_path": str(summary.path),
        "processed_at": datetime.utcnow().isoformat() + "Z",
    }


def _aggregate_charge_discharge_by_cycle(steps_df: pd.DataFrame) -> Dict[int, Dict[str, Optional[float]]]:
    """
    Aggregate capacity and energy from step rows by cycle, split into charge vs discharge.
    Charge steps: step_type starts with CHARGE. Discharge: step_type starts with DISCHARGE.
    Returns {cycle_no: {capacity_charge_ah, capacity_discharge_ah, energy_charge_wh, energy_discharge_wh}}.
    """
    cycle_col = next((c for c in steps_df.columns if "cycle" in c.lower()), None)
    if cycle_col is None:
        return {}

    cap_col = _find_col(steps_df, "capacity_ah")
    en_col = _find_col(steps_df, "energy_wh")
    step_type_col = "step_type" if "step_type" in steps_df.columns else None
    if not step_type_col or cap_col is None or en_col is None:
        return {}

    result: Dict[int, Dict[str, Optional[float]]] = {}
    for _, row in steps_df.iterrows():
        try:
            cy = int(row[cycle_col])
        except (ValueError, TypeError):
            continue
        step_type = str(row.get(step_type_col, "")).upper()
        cap = _get_val(row, steps_df, "capacity_ah")
        en = _get_val(row, steps_df, "energy_wh")

        if cy not in result:
            result[cy] = {
                "capacity_charge_ah": 0.0,
                "capacity_discharge_ah": 0.0,
                "energy_charge_wh": 0.0,
                "energy_discharge_wh": 0.0,
            }

        if step_type.startswith("CHARGE") and cap is not None:
            prev = result[cy]["capacity_charge_ah"]
            result[cy]["capacity_charge_ah"] = (prev if prev is not None else 0) + max(0, cap)
        if step_type.startswith("CHARGE") and en is not None:
            prev = result[cy]["energy_charge_wh"]
            result[cy]["energy_charge_wh"] = (prev if prev is not None else 0) + max(0, en)
        if step_type.startswith("DISCHARGE") and cap is not None:
            prev = result[cy]["capacity_discharge_ah"]
            result[cy]["capacity_discharge_ah"] = (prev if prev is not None else 0) + abs(cap)
        if step_type.startswith("DISCHARGE") and en is not None:
            prev = result[cy]["energy_discharge_wh"]
            result[cy]["energy_discharge_wh"] = (prev if prev is not None else 0) + abs(en)

    return result


def build_cycle_metrics_rows(
    run_id: str,
    index_id: str,
    cycles_df: pd.DataFrame,
    steps_df: Optional[pd.DataFrame] = None,
) -> List[Dict[str, Any]]:
    """Build cycle_metrics rows from GNRL DataFrame. Optionally aggregate charge/discharge from steps."""
    cycle_col = next((c for c in cycles_df.columns if "cycle" in c.lower()), None)
    if cycle_col is None:
        return []

    charge_discharge = _aggregate_charge_discharge_by_cycle(steps_df) if steps_df is not None and not steps_df.empty else {}

    rows: List[Dict[str, Any]] = []
    for _, row in cycles_df.iterrows():
        try:
            cy = int(row[cycle_col])
        except (ValueError, TypeError):
            continue
        cd = charge_discharge.get(cy, {})

        r = {
            "run_id": run_id,
            "index_id": index_id,
            "cycle_no": cy,
            "duration_s": _get_val(row, cycles_df, "duration_s"),
            "voltage_end_v": _get_val(row, cycles_df, "voltage_v"),
            "current_end_a": _get_val(row, cycles_df, "current_a"),
            "temperature_c": _get_val(row, cycles_df, "temperature_c"),
            "esr_periodic_ohm": _get_val(row, cycles_df, "esr_periodic"),
            "capacity_ah": _get_val(row, cycles_df, "capacity_ah"),
            "energy_wh": _get_val(row, cycles_df, "energy_wh"),
            "capacity_charge_ah": cd.get("capacity_charge_ah"),
            "capacity_discharge_ah": cd.get("capacity_discharge_ah"),
            "energy_charge_wh": cd.get("energy_charge_wh"),
            "energy_discharge_wh": cd.get("energy_discharge_wh"),
            "capacitance_f": _get_val(row, cycles_df, "capacitance_f"),
            "esr_charge_ohm": _get_val(row, cycles_df, "esr_charge"),
            "esr_discharge_ohm": _get_val(row, cycles_df, "esr_discharge"),
            "leakage_current_a": _get_val(row, cycles_df, "leakage_a"),
            "coulombic_eff_pct": _get_val(row, cycles_df, "coulombic_eff_pct"),
            "energy_eff_pct": _get_val(row, cycles_df, "energy_eff_pct"),
        }
        rows.append(r)
    return rows


def build_step_metrics_rows(
    run_id: str,
    index_id: str,
    steps_df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """Build step_metrics rows from per-step DataFrame (with step_no, step_type)."""
    cycle_col = next((c for c in steps_df.columns if "cycle" in c.lower()), None)
    if cycle_col is None:
        return []

    rows: List[Dict[str, Any]] = []
    for _, row in steps_df.iterrows():
        try:
            cy = int(row[cycle_col])
        except (ValueError, TypeError):
            continue
        step_no = row.get("step_no", -1)
        step_tag = str(row.get("step_marker", ""))
        step_type = str(row.get("step_type", "UNKNOWN"))

        r = {
            "run_id": run_id,
            "index_id": index_id,
            "cycle_no": cy,
            "step_no": step_no,
            "step_tag": step_tag,
            "step_type": step_type,
            "duration_s": _get_val(row, steps_df, "duration_s"),
            "voltage_end_v": _get_val(row, steps_df, "voltage_v"),
            "current_end_a": _get_val(row, steps_df, "current_a"),
            "temperature_c": _get_val(row, steps_df, "temperature_c"),
            "esr_periodic_ohm": _get_val(row, steps_df, "esr_periodic"),
            "capacity_ah": _get_val(row, steps_df, "capacity_ah"),
            "energy_wh": _get_val(row, steps_df, "energy_wh"),
            "capacitance_f": _get_val(row, steps_df, "capacitance_f"),
            "esr_charge_ohm": _get_val(row, steps_df, "esr_charge"),
            "esr_discharge_ohm": _get_val(row, steps_df, "esr_discharge"),
            "leakage_current_a": _get_val(row, steps_df, "leakage_a"),
            "coulombic_eff_pct": _get_val(row, steps_df, "coulombic_eff_pct"),
            "energy_eff_pct": _get_val(row, steps_df, "energy_eff_pct"),
        }
        rows.append(r)
    return rows


# RAW column aliases (Time,s, U,V, I,A, T,°C, Q,Ah, E,Wh)
_RAW_COL_ALIASES: Dict[str, List[str]] = {
    "time_s": ["time_continuous_s", "Time,s", "Time,s"],
    "voltage_v": ["U,V", "Ue,V"],
    "current_a": ["I,A", "Ie,A"],
    "temperature_c": ["T,°C", "Ta,°C", "T,C"],
    "capacity_ah": ["Q,Ah", "Q,Ah"],
    "energy_wh": ["E,Wh", "E,Wh"],
}


def _find_raw_col(df: pd.DataFrame, canonical: str) -> Optional[str]:
    aliases = _RAW_COL_ALIASES.get(canonical, [canonical])
    for a in aliases:
        for c in df.columns:
            if str(c).strip() == a:
                return c
    return None


def build_time_series_rows(
    run_id: str,
    index_id: str,
    raw_dfs: List[Tuple[int, pd.DataFrame]],
) -> List[Dict[str, Any]]:
    """
    Build time_series rows from RAW DataFrames.
    raw_dfs: list of (cycle_no, df) - cycle_no from filename or Cycle column.
    Time is concatenated across steps within each file; across files we add cycle offset.
    All RAW data is stored with no downsampling; first and last point of every cycle preserved.
    """
    rows: List[Dict[str, Any]] = []
    global_time_offset = 0.0

    for cycle_no, df in raw_dfs:
        if df.empty:
            continue
        time_col = _find_raw_col(df, "time_s")
        if time_col is None:
            continue
        max_time_in_file = df[time_col].max()
        if pd.isna(max_time_in_file):
            max_time_in_file = 0.0

        for seq, (_, row) in enumerate(df.iterrows()):
            t = row.get(time_col)
            if pd.isna(t):
                continue
            time_global = global_time_offset + float(t)
            r = {
                "run_id": run_id,
                "index_id": index_id,
                "cycle_no": cycle_no,
                "step_no": int(row.get("step_no", -1)),
                "step_type": str(row.get("step_type", "UNKNOWN")),
                "seq": seq,
                "time_s": time_global,
                "voltage_v": _safe_float(row, df, "voltage_v"),
                "current_a": _safe_float(row, df, "current_a"),
                "temperature_c": _safe_float(row, df, "temperature_c"),
                "capacity_ah": _safe_float(row, df, "capacity_ah"),
                "energy_wh": _safe_float(row, df, "energy_wh"),
            }
            rows.append(r)

        global_time_offset += max_time_in_file + 0.01  # small gap between cycles

    return rows


def _safe_float(row: pd.Series, df: pd.DataFrame, canonical: str) -> Optional[float]:
    col = _find_raw_col(df, canonical)
    if col is None:
        return None
    val = row.get(col)
    if pd.isna(val):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def build_esr_computed_rows(
    run_id: str,
    index_id: str,
    raw_dfs: List[Tuple[int, pd.DataFrame]],
    config: Optional[AppConfig] = None,
) -> List[Dict[str, Any]]:
    """Build esr_computed rows from RAW DataFrames (per cycle, discharge-to-rest). Skips SNU/SCAN cycles."""
    cfg = config or AppConfig.load()
    rows: List[Dict[str, Any]] = []
    for cycle_no, df in raw_dfs:
        if df.empty:
            continue
        # Skip SNU/SCAN cycles - no real discharge-to-rest
        if "step_type" in df.columns:
            scan_mask = df["step_type"].astype(str).str.upper().str.contains("SCAN|SNU", na=False)
            if scan_mask.all():
                continue
        results = compute_esr_from_discharge_rest(
            df,
            delays_s=cfg.esr.delays_s,
            strict_mode=cfg.esr.strict_mode,
            tolerance_s=cfg.esr.tolerance_s,
        )
        step_no = int(df["step_no"].iloc[0]) if "step_no" in df.columns else -1
        for r in results:
            rows.append({
                "run_id": run_id,
                "index_id": index_id,
                "cycle_no": int(cycle_no),
                "step_no": step_no,
                "direction": "discharge",
                "delay_s": r.delay_s,
                "esr_ohm": None if (r.esr_ohm != r.esr_ohm) else r.esr_ohm,  # NaN -> None
                "v_end_v": r.v_end_v,
                "v_at_delay_v": r.v_at_delay_v,
                "current_a": r.current_a,
                "sample_rate_hz": r.sample_rate_hz,
                "is_approximate": r.is_approximate,
                "reason": r.reason or None,
            })
    return rows


def _cycle_no_from_raw_path(path: Path) -> int:
    """Extract cycle number from RAW filename, e.g. object_id-00000001.txt -> 1."""
    stem = path.stem
    if "-" in stem:
        suffix = stem.split("-")[-1]
        try:
            return int(suffix)
        except ValueError:
            pass
    return 0


def process_and_build_rows(
    summary: FileSummary,
    config: Optional[AppConfig] = None,
    include_raw: bool = True,
) -> tuple[str, Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Process a CLK file and build all BigQuery rows.
    Returns (run_id, test_runs_row, cycle_metrics_rows, step_metrics_rows, time_series_rows, esr_computed_rows, curves_rows).
    """
    cfg = config or AppConfig.load()
    run_id = compute_run_id(summary.path)
    steps_df, cycles_df = read_clk_with_metrics(summary.path)
    header = parse_header(summary.path)

    tr = build_test_runs_rows(summary, run_id, header)
    cm = build_cycle_metrics_rows(run_id, summary.index_id, cycles_df, steps_df=steps_df)
    sm = build_step_metrics_rows(run_id, summary.index_id, steps_df)

    ts_rows: List[Dict[str, Any]] = []
    esr_rows: List[Dict[str, Any]] = []
    curve_rows: List[Dict[str, Any]] = []
    if include_raw:
        raw_dfs_list: List[Tuple[int, pd.DataFrame]] = []
        for raw_path, df in read_all_raw_for_clk(summary.path):
            cy = _cycle_no_from_raw_path(raw_path)
            cycle_col = next((c for c in df.columns if "cycle" in c.lower()), None)
            if cycle_col is not None and not df.empty:
                try:
                    cy = int(df[cycle_col].iloc[0])
                except (ValueError, TypeError, IndexError):
                    pass
            raw_dfs_list.append((cy, df))
        if raw_dfs_list:
            ts_rows = build_time_series_rows(run_id, summary.index_id, raw_dfs_list)
            esr_rows = build_esr_computed_rows(
                run_id, summary.index_id, raw_dfs_list, config=cfg
            )
            curve_rows = build_curves_rows(
                ts_rows,
                points_per_cycle_cycling=cfg.curves.points_per_cycle_cycling,
                points_per_cycle_cv=cfg.curves.points_per_cycle_cv,
            )

    return run_id, tr, cm, sm, ts_rows, esr_rows, curve_rows


def _fingerprints_match(
    client: Any,
    run_fingerprints_table: str,
    run_id: str,
    new_fps: Dict[str, str],
) -> bool:
    """Return True if all table fingerprints match existing in run_fingerprints."""
    try:
        q = f"SELECT table_name, fingerprint FROM `{run_fingerprints_table}` WHERE run_id = {repr(run_id)}"
        rows = list(client.query(q).result())
        existing = {r.table_name: r.fingerprint for r in rows}
        if len(existing) != len(new_fps):
            return False
        return all(existing.get(t) == fp for t, fp in new_fps.items())
    except Exception:
        return False


def stage_only(
    summaries: Iterator[FileSummary],
    config: Optional[AppConfig] = None,
    include_raw: bool = True,
    force: bool = False,
    force_run_ids: Optional[Set[str]] = None,
) -> Tuple[Optional[Path], int, int, int, List[str]]:
    """
    Process summaries and stage to Parquet only (no GCS/BigQuery upload).
    Returns (batch_path, staged_count, skipped_count, error_count, error_messages).
    batch_path is None if no files to stage.
    """
    cfg = config or AppConfig.load()
    force_run_ids = force_run_ids or set()

    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError:
        return None, 0, 0, 0, ["google-cloud-bigquery or google-auth not installed"]

    key_path = cfg.bq.service_account_key
    if key_path:
        key_path = Path(key_path)
        if not key_path.is_absolute():
            _tool_dir = Path(__file__).resolve().parent.parent.parent
            key_path = _tool_dir / key_path
        key_path = str(key_path) if key_path.exists() else None

    credentials = None
    if key_path:
        credentials = service_account.Credentials.from_service_account_file(key_path)

    project = cfg.bq.project
    dataset = cfg.bq.dataset
    client = bigquery.Client(project=project, credentials=credentials)
    run_fingerprints_table = f"{project}.{dataset}.run_fingerprints"

    skipped_count = 0
    errors: List[str] = []
    all_run_ids: List[str] = []
    uploaded_summaries: List[Tuple[str, FileSummary]] = []
    all_tr: List[Dict[str, Any]] = []
    all_cm: List[Dict[str, Any]] = []
    all_sm: List[Dict[str, Any]] = []
    all_ts: List[Dict[str, Any]] = []
    all_esr: List[Dict[str, Any]] = []
    all_curves: List[Dict[str, Any]] = []

    for summary in summaries:
        try:
            run_id = compute_run_id(summary.path)
            skip = False
            if not force and run_id not in force_run_ids:
                fps = compute_fingerprints(summary.path, config=cfg)
                if _fingerprints_match(client, run_fingerprints_table, run_id, fps):
                    skip = True
            if skip:
                skipped_count += 1
                continue
            run_id, tr, cm, sm, ts, esr, curves = process_and_build_rows(
                summary, config=cfg, include_raw=include_raw
            )
            all_run_ids.append(run_id)
            uploaded_summaries.append((run_id, summary))
            all_tr.append(tr)
            all_cm.extend(cm)
            all_sm.extend(sm)
            all_ts.extend(ts)
            all_esr.extend(esr)
            all_curves.extend(curves)
        except Exception as e:
            errors.append(f"{summary.path}: {e}")

    if not all_run_ids:
        return None, 0, skipped_count, len(errors), errors

    batch_path = stage_to_parquet(
        all_run_ids,
        all_tr,
        all_cm,
        all_sm,
        all_ts,
        all_esr,
        all_curves,
        config=cfg,
        uploaded_summaries=uploaded_summaries,
    )
    return batch_path, len(all_run_ids), skipped_count, len(errors), errors


def upload_batch_to_bigquery(
    batch_dir: Path,
    config: Optional[AppConfig] = None,
) -> tuple[int, int, List[str]]:
    """
    Upload a staged batch to GCS and BigQuery.
    Returns (uploaded_count, error_count, error_messages).
    """
    cfg = config or AppConfig.load()
    batch_dir = Path(batch_dir)

    run_ids_path = batch_dir / "run_ids.txt"
    if not run_ids_path.exists():
        return 0, 1, [f"run_ids.txt not found in {batch_dir}"]
    run_ids = [r.strip() for r in run_ids_path.read_text(encoding="utf-8").splitlines() if r.strip()]
    if not run_ids:
        return 0, 1, ["No run_ids in batch"]

    fp_path = batch_dir / "run_fingerprints.json"
    fp_rows: List[Dict[str, Any]] = []
    if fp_path.exists():
        fp_rows = json.loads(fp_path.read_text(encoding="utf-8"))
    # If missing, skip fingerprint upsert (no warning per plan - just skip)

    key_path = cfg.bq.service_account_key
    if key_path:
        key_path = Path(key_path)
        if not key_path.is_absolute():
            _tool_dir = Path(__file__).resolve().parent.parent.parent
            key_path = _tool_dir / key_path
        key_path = str(key_path) if key_path.exists() else None

    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError:
        return 0, 1, ["google-cloud-bigquery or google-auth not installed"]

    credentials = None
    if key_path:
        credentials = service_account.Credentials.from_service_account_file(key_path)

    project = cfg.bq.project
    dataset = cfg.bq.dataset
    client = bigquery.Client(project=project, credentials=credentials)
    test_runs_table = f"{project}.{dataset}.test_runs"
    cycle_metrics_table = f"{project}.{dataset}.cycle_metrics"
    step_metrics_table = f"{project}.{dataset}.step_metrics"
    time_series_table = f"{project}.{dataset}.time_series"
    esr_computed_table = f"{project}.{dataset}.esr_computed"
    curves_table = f"{project}.{dataset}.curves"
    run_fingerprints_table = f"{project}.{dataset}.run_fingerprints"

    errors: List[str] = []
    run_ids_str = ", ".join(repr(r) for r in run_ids)

    for table in [curves_table, time_series_table, esr_computed_table, cycle_metrics_table, step_metrics_table, test_runs_table]:
        try:
            client.query(f"DELETE FROM `{table}` WHERE run_id IN ({run_ids_str})").result()
        except Exception as e:
            errors.append(f"Delete {table}: {e}")

    try:
        client.query(f"DELETE FROM `{run_fingerprints_table}` WHERE run_id IN ({run_ids_str})").result()
    except Exception as e:
        errors.append(f"Delete run_fingerprints: {e}")

    use_gcs = bool(cfg.gcs.gcs_bucket)
    gcs_uris: Dict[str, str] = {}
    if use_gcs:
        try:
            gcs_uris = upload_staging_to_gcs(
                batch_dir,
                cfg.gcs.gcs_bucket,
                cfg.gcs.gcs_staging_prefix,
                credentials=credentials,
            )
        except Exception as e:
            errors.append(f"GCS upload: {e}")
            use_gcs = False

    _table_map = {
        "test_runs": test_runs_table,
        "cycle_metrics": cycle_metrics_table,
        "step_metrics": step_metrics_table,
        "time_series": time_series_table,
        "esr_computed": esr_computed_table,
        "curves": curves_table,
    }

    if use_gcs and gcs_uris:
        for name, uri in gcs_uris.items():
            table_id = _table_map.get(name)
            if table_id:
                try:
                    table_ref = client.get_table(table_id)
                    job_config = bigquery.LoadJobConfig(
                        source_format=bigquery.SourceFormat.PARQUET,
                        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                        schema=table_ref.schema,
                    )
                    job = client.load_table_from_uri(uri, table_id, job_config=job_config)
                    job.result()
                except Exception as e:
                    errors.append(f"Load {name} from GCS: {e}")
    else:
        for name in _TABLE_NAMES:
            parquet_path = batch_dir / f"{name}.parquet"
            if parquet_path.exists() and name in _table_map:
                try:
                    df = pd.read_parquet(parquet_path)
                    if not df.empty:
                        job_config = bigquery.LoadJobConfig(
                            autodetect=True,
                            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                        )
                        client.load_table_from_dataframe(
                            df, _table_map[name], job_config=job_config
                        ).result()
                except Exception as e:
                    errors.append(f"Load {name} from Parquet: {e}")

    if fp_rows:
        try:
            df_fp = pd.DataFrame(fp_rows)
            df_fp["updated_at"] = pd.to_datetime(df_fp["updated_at"])
            job_config = bigquery.LoadJobConfig(
                autodetect=True,
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            )
            client.load_table_from_dataframe(
                df_fp, run_fingerprints_table, job_config=job_config
            ).result()
        except Exception as e:
            errors.append(f"Upsert run_fingerprints: {e}")

    return len(run_ids), len(errors), errors


def upload_processed_files(
    summaries: Iterator[FileSummary],
    config: Optional[AppConfig] = None,
    dry_run: bool = False,
    include_raw: bool = True,
    force: bool = False,
    force_run_ids: Optional[Set[str]] = None,
) -> tuple[int, int, int, List[str]]:
    """
    Process summaries, build rows, and upload to BigQuery.
    Returns (uploaded_count, skipped_count, error_count, error_messages).
    If force or force_run_ids: ignore fingerprint, process and upload.
    Else: skip files whose per-table fingerprints all match.
    """
    cfg = config or AppConfig.load()
    project = cfg.bq.project
    dataset = cfg.bq.dataset
    key_path = cfg.bq.service_account_key
    if key_path:
        key_path = Path(key_path)
        if not key_path.is_absolute():
            _tool_dir = Path(__file__).resolve().parent.parent.parent
            key_path = _tool_dir / key_path
        key_path = str(key_path) if key_path.exists() else None
    force_run_ids = force_run_ids or set()

    if dry_run:
        count = 0
        for s in summaries:
            _ = process_and_build_rows(s, config=cfg, include_raw=include_raw)
            count += 1
        return count, 0, 0, []

    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError:
        return 0, 0, 0, ["google-cloud-bigquery or google-auth not installed"]

    if key_path:
        credentials = service_account.Credentials.from_service_account_file(key_path)
    else:
        credentials = None  # use default (ADC)

    client = bigquery.Client(project=project, credentials=credentials)

    test_runs_table = f"{project}.{dataset}.test_runs"
    cycle_metrics_table = f"{project}.{dataset}.cycle_metrics"
    step_metrics_table = f"{project}.{dataset}.step_metrics"
    time_series_table = f"{project}.{dataset}.time_series"
    esr_computed_table = f"{project}.{dataset}.esr_computed"
    curves_table = f"{project}.{dataset}.curves"
    run_fingerprints_table = f"{project}.{dataset}.run_fingerprints"

    skipped_count = 0
    all_run_ids: List[str] = []
    uploaded_summaries: List[Tuple[str, FileSummary]] = []
    all_tr: List[Dict[str, Any]] = []
    all_cm: List[Dict[str, Any]] = []
    all_sm: List[Dict[str, Any]] = []
    all_ts: List[Dict[str, Any]] = []
    all_esr: List[Dict[str, Any]] = []
    all_curves: List[Dict[str, Any]] = []
    errors: List[str] = []

    for summary in summaries:
        try:
            run_id = compute_run_id(summary.path)
            skip = False
            if not force and run_id not in force_run_ids:
                fps = compute_fingerprints(summary.path, config=cfg)
                if _fingerprints_match(client, run_fingerprints_table, run_id, fps):
                    skip = True
            if skip:
                skipped_count += 1
                continue
            run_id, tr, cm, sm, ts, esr, curves = process_and_build_rows(
                summary, config=cfg, include_raw=include_raw
            )
            all_run_ids.append(run_id)
            uploaded_summaries.append((run_id, summary))
            all_tr.append(tr)
            all_cm.extend(cm)
            all_sm.extend(sm)
            all_ts.extend(ts)
            all_esr.extend(esr)
            all_curves.extend(curves)
        except Exception as e:
            errors.append(f"{summary.path}: {e}")

    if not all_run_ids:
        return 0, skipped_count, len(errors), errors

    # Stage to Parquet before upload (for retry/offline)
    stage_path: Optional[Path] = None
    try:
        stage_path = stage_to_parquet(
            all_run_ids,
            all_tr,
            all_cm,
            all_sm,
            all_ts,
            all_esr,
            all_curves,
            config=cfg,
            uploaded_summaries=uploaded_summaries,
        )
    except Exception as e:
        errors.append(f"Staging: {e}")

    # Delete existing rows for these run_ids
    run_ids_str = ", ".join(repr(r) for r in all_run_ids)
    for table in [curves_table, time_series_table, esr_computed_table, cycle_metrics_table, step_metrics_table, test_runs_table]:
        try:
            q = f"DELETE FROM `{table}` WHERE run_id IN ({run_ids_str})"
            client.query(q).result()
        except Exception as e:
            errors.append(f"Delete {table}: {e}")

    use_gcs = bool(cfg.gcs.gcs_bucket and stage_path)
    gcs_uris: Dict[str, str] = {}

    if use_gcs:
        try:
            gcs_uris = upload_staging_to_gcs(
                stage_path,
                cfg.gcs.gcs_bucket,
                cfg.gcs.gcs_staging_prefix,
                credentials=credentials,
            )
        except Exception as e:
            errors.append(f"GCS upload: {e}")
            use_gcs = False

    if use_gcs and gcs_uris:
        # Load from GCS (memory-efficient)
        _table_map = {
            "test_runs": test_runs_table,
            "cycle_metrics": cycle_metrics_table,
            "step_metrics": step_metrics_table,
            "time_series": time_series_table,
            "esr_computed": esr_computed_table,
            "curves": curves_table,
        }
        for name, uri in gcs_uris.items():
            table_id = _table_map.get(name)
            if table_id:
                try:
                    table_ref = client.get_table(table_id)
                    job_config = bigquery.LoadJobConfig(
                        source_format=bigquery.SourceFormat.PARQUET,
                        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                        schema=table_ref.schema,
                    )
                    job = client.load_table_from_uri(uri, table_id, job_config=job_config)
                    job.result()
                except Exception as e:
                    errors.append(f"Load {name} from GCS: {e}")
    else:
        # Fallback: load from memory (original behavior)
        def insert_json(table: str, rows: List[Dict[str, Any]]) -> None:
            if not rows:
                return
            job_config = bigquery.LoadJobConfig(
                autodetect=True,
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            )
            df = pd.DataFrame(rows)
            job = client.load_table_from_dataframe(df, table, job_config=job_config)
            job.result()

        try:
            insert_json(test_runs_table, all_tr)
            insert_json(cycle_metrics_table, all_cm)
            insert_json(step_metrics_table, all_sm)
            insert_json(time_series_table, all_ts)
            insert_json(esr_computed_table, all_esr)
            insert_json(curves_table, all_curves)
        except Exception as e:
            errors.append(f"Insert: {e}")

    # Upsert run_fingerprints for uploaded runs
    if all_run_ids and uploaded_summaries:
        try:
            run_ids_str = ", ".join(repr(r) for r in all_run_ids)
            client.query(
                f"DELETE FROM `{run_fingerprints_table}` WHERE run_id IN ({run_ids_str})"
            ).result()
            fp_rows: List[Dict[str, Any]] = []
            now = pd.Timestamp.utcnow()
            for run_id, summary in uploaded_summaries:
                fps = compute_fingerprints(summary.path, config=cfg)
                for table_name, fp in fps.items():
                    fp_rows.append({
                        "run_id": run_id,
                        "table_name": table_name,
                        "fingerprint": fp,
                        "updated_at": now,
                    })
            if fp_rows:
                df_fp = pd.DataFrame(fp_rows)
                df_fp["updated_at"] = pd.to_datetime(df_fp["updated_at"])
                job_config = bigquery.LoadJobConfig(
                    autodetect=True,
                    write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                )
                client.load_table_from_dataframe(
                    df_fp, run_fingerprints_table, job_config=job_config
                ).result()
        except Exception as e:
            errors.append(f"Upsert run_fingerprints: {e}")

    return len(all_tr), skipped_count, len(errors), errors
