"""
Curve generation from time_series data for Looker Studio.
Produces downsampled curve points: CYCLING (time vs V, I), CV (V vs I for SNU).

Default mode downsamples per (cycle_no, step_no) so step boundaries (Charge CC, Discharge CC,
RLAX, etc.) retain first/last samples — better for dashboard point budgets than whole-cycle bins.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from geyser_tool.config import CurvesConfig


def _sample_indices(n: int, k: int) -> List[int]:
    """Sample k indices from 0..n-1, always including 0 and n-1. Preserves order."""
    if n <= k:
        return list(range(n))
    if k <= 1:
        return [0] if n else []
    return [int(round(i * (n - 1) / (k - 1))) for i in range(k)]


def _downsample_grouped(
    df: pd.DataFrame,
    group_cols: List[str],
    sort_cols: List[str],
    max_per_group: int,
) -> pd.DataFrame:
    """For each group key, keep up to max_per_group rows (first and last always when k > 2)."""
    if df.empty:
        return df
    for c in group_cols:
        if c not in df.columns:
            return pd.DataFrame()
    df = df.sort_values(sort_cols).reset_index(drop=True)
    sampled_rows: List[pd.Series] = []
    for _, g in df.groupby(group_cols, sort=True):
        g = g.reset_index(drop=True)
        n = len(g)
        k = min(max(1, max_per_group), n)
        for i in _sample_indices(n, k):
            sampled_rows.append(g.iloc[i])
    if not sampled_rows:
        return pd.DataFrame()
    return pd.DataFrame(sampled_rows).reset_index(drop=True)


def build_cycling_curves(
    time_series_rows: List[Dict[str, Any]],
    *,
    config: Optional[CurvesConfig] = None,
    points_per_cycle: int | None = None,
) -> List[Dict[str, Any]]:
    """
    Build CYCLING curve points: x=time_s, y=voltage_v, y2=current_a.
    Excludes SNU/SCAN steps.
    """
    cfg = config or CurvesConfig()
    seg_max = cfg.max_points_per_cycling_segment
    if points_per_cycle is not None:
        seg_max = points_per_cycle

    if not time_series_rows:
        return []
    df = pd.DataFrame(time_series_rows)
    if "time_s" not in df.columns or "voltage_v" not in df.columns:
        return []

    step_type_col = "step_type" if "step_type" in df.columns else None
    if step_type_col:
        mask = ~df[step_type_col].astype(str).str.upper().str.contains("SCAN|SNU", na=False)
        df = df[mask]
    if df.empty:
        return []

    if "cycle_no" not in df.columns:
        return []

    if cfg.curves_per_step_segment and "step_no" in df.columns:
        out = _downsample_grouped(
            df,
            group_cols=["cycle_no", "step_no"],
            sort_cols=["cycle_no", "step_no", "time_s"],
            max_per_group=seg_max,
        )
    else:
        out = _downsample_grouped(
            df,
            group_cols=["cycle_no"],
            sort_cols=["cycle_no", "time_s"],
            max_per_group=cfg.points_per_cycle_cycling if points_per_cycle is None else seg_max,
        )

    if out.empty:
        return []

    rows: List[Dict[str, Any]] = []
    for seq, (_, r) in enumerate(out.iterrows()):
        rows.append({
            "run_id": r.get("run_id"),
            "index_id": r.get("index_id"),
            "cycle_no": int(r.get("cycle_no", 0)),
            "step_no": int(r.get("step_no", -1)),
            "curve_type": "CYCLING",
            "seq": seq,
            "x": r.get("time_s"),
            "y": r.get("voltage_v"),
            "y2": r.get("current_a"),
        })
    return rows


def _effective_cv_cap(cfg: CurvesConfig, sample_rate_hz: Optional[float]) -> int:
    if (
        sample_rate_hz is not None
        and sample_rate_hz > 0
        and sample_rate_hz <= cfg.min_points_per_cv_segment_if_low_rate_hz
    ):
        return cfg.max_points_per_cv_segment_low_rate
    return cfg.max_points_per_cv_segment


def build_cv_curves(
    time_series_rows: List[Dict[str, Any]],
    *,
    config: Optional[CurvesConfig] = None,
    points_per_cycle: int | None = None,
    sample_rate_hz: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Build CV curve points from SNU/SCAN_V steps: x=voltage_v, y=current_a.
    """
    cfg = config or CurvesConfig()
    seg_max = _effective_cv_cap(cfg, sample_rate_hz)
    if points_per_cycle is not None:
        seg_max = points_per_cycle

    if not time_series_rows:
        return []
    df = pd.DataFrame(time_series_rows)
    if "voltage_v" not in df.columns or "current_a" not in df.columns:
        return []

    step_type_col = "step_type" if "step_type" in df.columns else None
    if step_type_col:
        mask = df[step_type_col].astype(str).str.upper().str.contains("SCAN|SNU", na=False)
        df = df[mask]
    if df.empty:
        return []

    if "cycle_no" not in df.columns:
        return []

    sort_base = ["cycle_no", "time_s"] if "time_s" in df.columns else ["cycle_no", "voltage_v"]

    if cfg.curves_per_step_segment and "step_no" in df.columns:
        sort_cols = ["cycle_no", "step_no"] + [c for c in sort_base if c not in ("cycle_no", "step_no")]
        out = _downsample_grouped(
            df,
            group_cols=["cycle_no", "step_no"],
            sort_cols=sort_cols,
            max_per_group=seg_max,
        )
    else:
        out = _downsample_grouped(
            df,
            group_cols=["cycle_no"],
            sort_cols=sort_base,
            max_per_group=cfg.points_per_cycle_cv if points_per_cycle is None else seg_max,
        )

    if out.empty:
        return []

    rows: List[Dict[str, Any]] = []
    for seq, (_, r) in enumerate(out.iterrows()):
        rows.append({
            "run_id": r.get("run_id"),
            "index_id": r.get("index_id"),
            "cycle_no": int(r.get("cycle_no", 0)),
            "step_no": int(r.get("step_no", -1)),
            "curve_type": "CV",
            "seq": seq,
            "x": r.get("voltage_v"),
            "y": r.get("current_a"),
            "y2": None,
        })
    return rows


def build_curves_rows(
    time_series_rows: List[Dict[str, Any]],
    curve_types: List[str] | None = None,
    *,
    config: Optional[CurvesConfig] = None,
    sample_rate_hz: Optional[float] = None,
    # Backward compatibility for tests / old call sites
    points_per_cycle_cycling: int | None = None,
    points_per_cycle_cv: int | None = None,
) -> List[Dict[str, Any]]:
    """
    Build curve rows for Looker. curve_types: ["CYCLING", "CV"] (dQ/dV deferred).

    ``config`` controls per-step vs per-cycle bucketing and segment point caps.
    ``sample_rate_hz`` (from CLK header) tightens CV caps on low-rate runs.
    """
    if curve_types is None:
        curve_types = ["CYCLING", "CV"]
    cfg = config or CurvesConfig()
    rows: List[Dict[str, Any]] = []
    if "CYCLING" in curve_types:
        rows.extend(
            build_cycling_curves(
                time_series_rows,
                config=cfg,
                points_per_cycle=points_per_cycle_cycling,
            )
        )
    if "CV" in curve_types:
        rows.extend(
            build_cv_curves(
                time_series_rows,
                config=cfg,
                points_per_cycle=points_per_cycle_cv,
                sample_rate_hz=sample_rate_hz,
            )
        )
    for i, r in enumerate(rows):
        r["seq"] = i
    return rows
