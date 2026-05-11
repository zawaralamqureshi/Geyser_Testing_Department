"""
Per-table fingerprint computation from file metadata (no content read).
Used for smart skip: when fingerprint matches, skip processing/upload.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from geyser_tool.config import AppConfig
from geyser_tool.parsers.raw_reader import find_raw_files_for_clk

_TABLE_NAMES = (
    "test_runs",
    "cycle_metrics",
    "step_metrics",
    "time_series",
    "esr_computed",
    "curves",
)


def _file_payload(path: Path) -> str:
    """Deterministic payload from file path, size, mtime."""
    try:
        stat = path.stat()
        return f"{path.resolve()}|{stat.st_size}|{stat.st_mtime}"
    except OSError:
        return str(path.resolve())


def _hash_payload(payload: str) -> str:
    """SHA256 hash of payload, truncated to 24 chars for storage."""
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def compute_fingerprints(clk_path: Path, config: AppConfig | None = None) -> Dict[str, str]:
    """
    Compute per-table fingerprints from file metadata (no content read).
    Returns dict mapping table_name -> fingerprint.
    """
    cfg = config or AppConfig.load()
    clk_path = Path(clk_path).resolve()

    clk_payload = _file_payload(clk_path)
    raw_paths = find_raw_files_for_clk(clk_path)
    raw_payloads = [_file_payload(p) for p in raw_paths]
    raw_combined = "|".join(sorted(raw_payloads))

    # CLK-only tables (test_runs, cycle_metrics, step_metrics)
    fp_clk = _hash_payload(clk_payload)

    # time_series: CLK + all RAW files
    fp_time_series = _hash_payload(clk_payload + "|" + raw_combined) if raw_payloads else fp_clk

    # esr_computed: CLK + RAW + ESR config
    esr_cfg = json.dumps(
        [cfg.esr.delays_s, cfg.esr.strict_mode, cfg.esr.tolerance_s],
        sort_keys=True,
    )
    fp_esr = _hash_payload(clk_payload + "|" + raw_combined + "|" + esr_cfg) if raw_payloads else fp_clk

    # curves: CLK + RAW + curves config
    curves_cfg = json.dumps(
        [cfg.curves.points_per_cycle_cycling, cfg.curves.points_per_cycle_cv],
        sort_keys=True,
    )
    fp_curves = _hash_payload(clk_payload + "|" + raw_combined + "|" + curves_cfg) if raw_payloads else fp_clk

    return {
        "test_runs": fp_clk,
        "cycle_metrics": fp_clk,
        "step_metrics": fp_clk,
        "time_series": fp_time_series,
        "esr_computed": fp_esr,
        "curves": fp_curves,
    }
