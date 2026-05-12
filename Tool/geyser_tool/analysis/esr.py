"""
ESR from RAW (discharge → RLX/RLAX boundary method).

Eligible ``protocol_detected`` values: ``STANDARD_CELL``, ``BLOCK_CYCLING``, ``CYCLABILITY``.
The CLK program must mention a 1 s rest (e.g. ``Rest 1s`` or ``Rest during 1s`` — see
:func:`program_has_rest_1s`).

For each discharge segment (markers DCC, DCCC, DCHCC) immediately followed by relax (RLX or RLAX):

- ``v_end_v`` / denominator: last sample of the discharge step (voltage / |I|).
- ``delay_s`` ≈ 0.01 s row: ``v_at_delay_v`` = **first** sample of the RLX step (step boundary).
- ``delay_s`` = 1.0 s row: ``v_at_delay_v`` = **last** sample of that RLX step.

Other protocols, or programs without a matching rest phrase, produce **no** ESR rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from geyser_tool.parsers.raw_reader import _parse_step_marker

# Boundary ESR is enabled for these ``test_runs.protocol_detected`` values when program text matches.
_ESR_PROTOCOL_LABELS = frozenset({
    "STANDARD_CELL",
    "BLOCK_CYCLING",
    "CYCLABILITY",
})

_DISCHARGE_MARKERS = frozenset({"DCC", "DCCC", "DCHCC"})
_RELAX_MARKERS = frozenset({"RLX", "RLAX"})


@dataclass
class ESRResult:
    """One ESR value at a configured delay (discharge → RLX boundary semantics)."""

    delay_s: float
    esr_ohm: float
    v_end_v: float
    v_at_delay_v: float
    current_a: float
    sample_rate_hz: float
    is_approximate: bool
    reason: str
    step_no: int = -1


def _find_col(df: pd.DataFrame, names: List[str]) -> Optional[str]:
    for n in names:
        for c in df.columns:
            if str(c).strip() == n:
                return c
    return None


def program_has_rest_1s(program_text: Optional[str]) -> bool:
    """True if program text indicates a 1 s rest (``Rest 1s``, ``Rest during 1s``, etc.)."""
    if not program_text:
        return False
    low = program_text.lower()
    if "rest 1s" in low:
        return True
    if "rest during 1s" in low:
        return True
    if re.search(r"rest\s+during\s*1\s*s", low):
        return True
    return bool(re.search(r"rest\s*1\s*s", low))


def _median_sample_rate_hz(df: pd.DataFrame, time_col: str) -> float:
    if len(df) <= 1:
        return 10.0
    dts = df[time_col].diff().dropna()
    if len(dts) == 0:
        return 10.0
    dt = float(dts.median())
    return (1.0 / dt) if dt > 0 else 10.0


def _step_column(df: pd.DataFrame) -> Optional[str]:
    return next((c for c in df.columns if str(c).lower().startswith("step")), None)


def _segment_marker(df_seg: pd.DataFrame, step_col: str) -> tuple[int, str]:
    """Marker from ``step_marker`` column or parsed ``Step`` cell."""
    if "step_marker" in df_seg.columns:
        raw_m = df_seg["step_marker"].iloc[0]
        if pd.notna(raw_m) and str(raw_m).strip():
            m = str(raw_m).strip().upper()
            sn = int(df_seg["step_no"].iloc[0]) if "step_no" in df_seg.columns else -1
            return sn, m
    raw_step = df_seg[step_col].iloc[0]
    sn, marker = _parse_step_marker(str(raw_step))
    return sn, marker.upper()


def _voltage_at_row(df: pd.DataFrame, idx: int, v_col: str) -> Optional[float]:
    try:
        v = float(df.loc[idx, v_col])
        if pd.isna(v):
            return None
        return v
    except (TypeError, ValueError, KeyError):
        return None


def _current_at_row(df: pd.DataFrame, idx: int, i_col: str) -> Optional[float]:
    try:
        v = float(df.loc[idx, i_col])
        if pd.isna(v):
            return None
        return v
    except (TypeError, ValueError, KeyError):
        return None


def _delay_maps_to_boundary(delay_s: float) -> Optional[str]:
    """Map config delay to RLX boundary: first sample vs last sample."""
    if abs(delay_s - 0.010) < 1e-5:
        return "first_rlx"
    if abs(delay_s - 1.0) < 1e-6:
        return "last_rlx"
    return None


def compute_esr_standard_cell_boundary(
    df: pd.DataFrame,
    delays_s: Optional[List[float]] = None,
    *,
    protocol_label: str,
    program_text: Optional[str],
) -> List[ESRResult]:
    """
    Boundary-based ESR (discharge → RLX/RLAX).

    Preconditions (else returns empty list):

    - ``protocol_label`` is one of ``STANDARD_CELL``, ``BLOCK_CYCLING``, ``CYCLABILITY``.
    - Program text matches ``program_has_rest_1s`` (e.g. ``Rest 1s``, ``Rest during 1s``).
    """
    if delays_s is None:
        delays_s = [0.010, 1.0]

    results: List[ESRResult] = []
    if protocol_label not in _ESR_PROTOCOL_LABELS:
        return results
    if not program_has_rest_1s(program_text):
        return results

    time_col = _find_col(df, ["time_continuous_s", "Time,s", "Time,s"])
    i_col = _find_col(df, ["I,A", "Ie,A"])
    v_col = _find_col(df, ["U,V", "Ue,V"])
    if not all([time_col, i_col, v_col]):
        return results

    step_col = _step_column(df)
    if step_col is None:
        return results

    work = df.reset_index(drop=True).copy()
    work["_seg"] = (work[step_col].astype(str) != work[step_col].astype(str).shift()).cumsum()
    segments = [g.reset_index(drop=True) for _, g in work.groupby("_seg", sort=True)]

    sample_hz = _median_sample_rate_hz(work, time_col)

    for i in range(len(segments) - 1):
        g_dis = segments[i]
        g_rlx = segments[i + 1]
        _, mk_dis = _segment_marker(g_dis, step_col)
        _, mk_rlx = _segment_marker(g_rlx, step_col)

        if mk_dis not in _DISCHARGE_MARKERS:
            continue
        if mk_rlx not in _RELAX_MARKERS:
            continue

        idx_last_d = g_dis.index[-1]
        v_end = _voltage_at_row(g_dis, idx_last_d, v_col)
        i_dis = _current_at_row(g_dis, idx_last_d, i_col)
        if v_end is None or i_dis is None:
            continue
        i_abs = abs(i_dis)
        if i_abs <= 1e-9:
            continue

        idx_first_r = g_rlx.index[0]
        idx_last_r = g_rlx.index[-1]
        v_first_rlx = _voltage_at_row(g_rlx, idx_first_r, v_col)
        v_last_rlx = _voltage_at_row(g_rlx, idx_last_r, v_col)

        step_no = -1
        if "step_no" in g_dis.columns:
            try:
                step_no = int(g_dis["step_no"].iloc[-1])
            except (ValueError, TypeError):
                step_no = -1

        for delay in delays_s:
            boundary = _delay_maps_to_boundary(float(delay))
            if boundary is None:
                continue
            if boundary == "first_rlx":
                v_at = v_first_rlx
                tag = "first_rlx_boundary"
            else:
                v_at = v_last_rlx
                tag = "last_rlx_boundary"

            if v_at is None:
                results.append(
                    ESRResult(
                        delay_s=float(delay),
                        esr_ohm=float("nan"),
                        v_end_v=v_end,
                        v_at_delay_v=float("nan"),
                        current_a=i_abs,
                        sample_rate_hz=sample_hz,
                        is_approximate=False,
                        reason="missing_voltage_rlx",
                        step_no=step_no,
                    )
                )
                continue

            esr = (v_at - v_end) / i_abs
            results.append(
                ESRResult(
                    delay_s=float(delay),
                    esr_ohm=esr,
                    v_end_v=v_end,
                    v_at_delay_v=v_at,
                    current_a=i_abs,
                    sample_rate_hz=sample_hz,
                    is_approximate=False,
                    reason=tag,
                    step_no=step_no,
                )
            )

    return results


def compute_esr_from_discharge_rest(
    df: pd.DataFrame,
    delays_s: Optional[List[float]] = None,
    strict_mode: bool = True,
    tolerance_s: float = 0.0,
    *,
    protocol_label: str = "",
    program_text: Optional[str] = None,
) -> List[ESRResult]:
    """
    Compute ESR rows for a RAW dataframe.

    Delegates to :func:`compute_esr_standard_cell_boundary`. The legacy current-threshold
    method is removed; ``strict_mode`` and ``tolerance_s`` are ignored (kept for call compatibility).
    """
    _ = strict_mode
    _ = tolerance_s
    return compute_esr_standard_cell_boundary(
        df,
        delays_s=delays_s,
        protocol_label=protocol_label,
        program_text=program_text,
    )
