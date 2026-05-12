from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml


@dataclass
class ESRConfig:
    delays_s: List[float] = field(default_factory=lambda: [0.010, 1.0])
    strict_mode: bool = True
    tolerance_s: float = 0.0


@dataclass
class PathsConfig:
    cells_root: Path = Path(r"D:\Electrical_tests\Cells")
    blocks_root: Path = Path(r"D:\Electrical_tests\Blocks")
    staging_root: Path = Path(r"D:\Electrical_tests\_staging")


@dataclass
class BigQueryConfig:
    project: str = "geyser-testing-department"
    dataset: str = "electrical_tests"
    service_account_key: Path | None = None


@dataclass
class GCSConfig:
    """GCS bucket for parquet staging. If empty, fall back to in-memory BigQuery upload."""
    gcs_bucket: str = ""
    gcs_staging_prefix: str = "staging/"


@dataclass
class RawTimeseriesConfig:
    """
    BigQuery `time_series` is built by concatenating one RAW file per logged cycle.

    Between files, upload adds ``inter_cycle_gap_s`` after each file's max time before
    stitching the next file. That gap is synthetic (not in RAW). Default is ``0`` (no gap);
    set e.g. ``0.01`` if you want a small delimiter between cycles.
    """

    inter_cycle_gap_s: float = 0.0


@dataclass
class CurvesConfig:
    max_points_per_run: int = 50_000  # Deprecated for time_series; kept for reference
    target_points_per_step: int | None = None  # Reserved for optional time_series downsampling (unused)
    points_per_cycle_cycling: int = 150  # Used when curves_per_step_segment is False (legacy whole-cycle buckets)
    points_per_cycle_cv: int = 1500
    # Per-(cycle, step_no): preserves step boundaries (Charge/Discharge/RLAX) within a cycle — Looker-friendly
    curves_per_step_segment: bool = True
    max_points_per_cycling_segment: int = 100
    max_points_per_cv_segment: int = 500
    # When sample_rate_hz <= this threshold, shrink CV segments (helps low-rate dashboards)
    min_points_per_cv_segment_if_low_rate_hz: float = 120.0
    max_points_per_cv_segment_low_rate: int = 200


@dataclass
class AppConfig:
    paths: PathsConfig = field(default_factory=PathsConfig)
    bq: BigQueryConfig = field(default_factory=BigQueryConfig)
    gcs: GCSConfig = field(default_factory=GCSConfig)
    esr: ESRConfig = field(default_factory=ESRConfig)
    raw_ts: RawTimeseriesConfig = field(default_factory=RawTimeseriesConfig)
    curves: CurvesConfig = field(default_factory=CurvesConfig)
    object_to_index_regex: str = r"(\d{6}).*?(G\d+)_(\d{3})"

    @classmethod
    def load(cls, path: Path | str | None = None) -> "AppConfig":
        if path is None:
            # Auto-load config.yaml from Tool directory (parent of geyser_tool package)
            _pkg_dir = Path(__file__).resolve().parent
            _tool_dir = _pkg_dir.parent
            _default_config = _tool_dir / "config.yaml"
            if _default_config.exists():
                path = _default_config
            else:
                return cls()
        p = Path(path)
        if not p.exists():
            return cls()
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        # For now, do a shallow merge; can be made richer later.
        cfg = cls()
        for section_name, section in (
            ("paths", cfg.paths),
            ("bq", cfg.bq),
            ("gcs", cfg.gcs),
            ("esr", cfg.esr),
            ("raw_ts", cfg.raw_ts),
            ("curves", cfg.curves),
        ):
            if section_name in data:
                for key, value in data[section_name].items():
                    if hasattr(section, key):
                        setattr(section, key, value)
        if "object_to_index_regex" in data:
            cfg.object_to_index_regex = data["object_to_index_regex"]
        return cfg

