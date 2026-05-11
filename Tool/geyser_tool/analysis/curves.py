"""
Curve generation from time_series data for Looker Studio.
Produces downsampled curve points: CYCLING (time vs V, I), CV (V vs I for SNU), dQ/dV.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd


def _sample_indices(n: int, k: int) -> List[int]:
    """Sample k indices from 0..n-1, always including 0 and n-1. Preserves order."""
    if n <= k:
        return list(range(n))
    if k <= 1:
        return [0] if n else []
    return [int(round(i * (n - 1) / (k - 1))) for i in range(k)]


def build_cycling_curves(
    time_series_rows: List[Dict[str, Any]],
    points_per_cycle: int = 150,
) -> List[Dict[str, Any]]:
    """
    Build CYCLING curve points from time_series: x=time_s, y=voltage_v, y2=current_a.
    Excludes SNU/SCAN steps (those go to CV curves). Uses fixed points per cycle with
    _sample_indices so first and last point of each cycle are always kept.
    """
    if not time_series_rows:
        return []
    df = pd.DataFrame(time_series_rows)
    if "time_s" not in df.columns or "voltage_v" not in df.columns:
        return []

    # Exclude SNU/SCAN steps (those belong in CV curves, not cycling)
    step_type_col = "step_type" if "step_type" in df.columns else None
    if step_type_col:
        mask = ~df[step_type_col].astype(str).str.upper().str.contains("SCAN|SNU", na=False)
        df = df[mask]
    if df.empty:
        return []

    # Per-cycle downsampling: group by cycle_no, sample fixed points per cycle.
    cycle_col = "cycle_no"
    if cycle_col not in df.columns:
        return []

    df = df.sort_values(["cycle_no", "time_s"]).reset_index(drop=True)
    groups = df.groupby(cycle_col, sort=True)

    sampled_rows: List[pd.Series] = []
    for cycle_no, cycle_df in groups:
        cycle_df = cycle_df.reset_index(drop=True)
        n = len(cycle_df)
        indices = _sample_indices(n, min(n, points_per_cycle))  # fixed 150 pts/cycle
        for i in indices:
            sampled_rows.append(cycle_df.iloc[i])

    if not sampled_rows:
        return []

    df = pd.DataFrame(sampled_rows).reset_index(drop=True)

    rows: List[Dict[str, Any]] = []
    for seq, (_, r) in enumerate(df.iterrows()):
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


def build_cv_curves(
    time_series_rows: List[Dict[str, Any]],
    points_per_cycle: int = 1500,
) -> List[Dict[str, Any]]:
    """
    Build CV curve points from SNU/SCAN_V steps: x=voltage_v, y=current_a.
    Filter by step_type containing SCAN or SNU. Per-cycle downsampling with
    _sample_indices so first and last point of each cycle are always kept.
    """
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

    cycle_col = "cycle_no"
    if cycle_col not in df.columns:
        return []

    sort_cols = ["cycle_no", "time_s"] if "time_s" in df.columns else ["cycle_no", "voltage_v"]
    df = df.sort_values(sort_cols).reset_index(drop=True)
    groups = df.groupby(cycle_col, sort=True)

    sampled_rows: List[pd.Series] = []
    for cycle_no, cycle_df in groups:
        cycle_df = cycle_df.reset_index(drop=True)
        n = len(cycle_df)
        indices = _sample_indices(n, min(n, points_per_cycle))
        for i in indices:
            sampled_rows.append(cycle_df.iloc[i])

    if not sampled_rows:
        return []

    df = pd.DataFrame(sampled_rows).reset_index(drop=True)

    rows: List[Dict[str, Any]] = []
    for seq, (_, r) in enumerate(df.iterrows()):
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
    points_per_cycle_cycling: int = 150,
    points_per_cycle_cv: int = 1500,
) -> List[Dict[str, Any]]:
    """
    Build curve rows for Looker. curve_types: ["CYCLING", "CV"] (dQ/dV deferred).
    """
    if curve_types is None:
        curve_types = ["CYCLING", "CV"]
    rows: List[Dict[str, Any]] = []
    if "CYCLING" in curve_types:
        rows.extend(build_cycling_curves(time_series_rows, points_per_cycle=points_per_cycle_cycling))
    if "CV" in curve_types:
        rows.extend(build_cv_curves(time_series_rows, points_per_cycle=points_per_cycle_cv))
    return rows
