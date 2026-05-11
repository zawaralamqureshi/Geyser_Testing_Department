"""
ESR recomputation from RAW data per SOP Section 3D (Standard Cell) and ToS 7.5 (VTT Test 1).
Locates discharge-to-rest edge, computes ESR at 10ms and 1s.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd


@dataclass
class ESRResult:
    """Recomputed ESR at a specific delay."""
    delay_s: float
    esr_ohm: float
    v_end_v: float
    v_at_delay_v: float
    current_a: float
    sample_rate_hz: float
    is_approximate: bool
    reason: str


def _find_col(df: pd.DataFrame, names: List[str]) -> Optional[str]:
    for n in names:
        for c in df.columns:
            if str(c).strip() == n:
                return c
    return None


def compute_esr_from_discharge_rest(
    df: pd.DataFrame,
    delays_s: List[float] = None,
    strict_mode: bool = True,
    tolerance_s: float = 0.0,
) -> List[ESRResult]:
    """
    Per SOP 3D: Find discharge-to-rest transition, then ESR at given delays.

    1. Find first sample where |I| <= 0.02 * |I_discharge| -> t=0
    2. VEND = last voltage where |I| >= 0.98 * |I_discharge|
    3. V10ms, V1s = voltage at nearest sample to t+10ms, t+1s
    4. ESR = (V_at_delay - VEND) / |I_discharge|

    strict_mode: ESR is NaN unless sample within 0.5*dt of target
    tolerance_s: if > 0, relaxed mode - allow sample within tolerance; flag is_approximate
    """
    if delays_s is None:
        delays_s = [0.010, 1.0]

    results: List[ESRResult] = []
    time_col = _find_col(df, ["time_continuous_s", "Time,s", "Time,s"])
    i_col = _find_col(df, ["I,A", "Ie,A"])
    v_col = _find_col(df, ["U,V", "Ue,V"])
    if not all([time_col, i_col, v_col]):
        return results

    # Find discharge region: |I| > threshold
    df = df.copy()
    df["_abs_i"] = df[i_col].abs()
    discharge_mask = df["_abs_i"] > 0.1  # at least 0.1A
    if not discharge_mask.any():
        return results

    discharge_region = df[discharge_mask]
    i_discharge = discharge_region[i_col].iloc[-1]  # use last discharge current
    i_threshold_high = abs(i_discharge) * 0.98
    i_threshold_low = abs(i_discharge) * 0.02

    # VEND = last sample with |I| >= 0.98 * I_discharge
    vend_mask = df["_abs_i"] >= i_threshold_high
    if not vend_mask.any():
        return results
    vend_idx = df[vend_mask].index[-1]
    vend = float(df.loc[vend_idx, v_col])

    # t=0 = first sample where |I| <= 0.02 * I_discharge (after discharge)
    rest_start_idx = None
    for idx in df.index:
        if idx <= vend_idx:
            continue
        if df.loc[idx, "_abs_i"] <= i_threshold_low:
            rest_start_idx = idx
            break
    if rest_start_idx is None:
        return results

    t0 = float(df.loc[rest_start_idx, time_col])
    dt = 1.0 / 10.0  # assume 10Hz if not detectable; could parse from header
    if len(df) > 1:
        dts = df[time_col].diff().dropna()
        if len(dts) > 0:
            dt = float(dts.median())
    sample_rate_hz = 1.0 / dt if dt > 0 else 10.0

    for delay in delays_s:
        target_t = t0 + delay
        # Find nearest sample to target_t
        df["_dist"] = (df[time_col] - target_t).abs()
        nearest_idx = df["_dist"].idxmin()
        nearest_dist = df.loc[nearest_idx, "_dist"]
        v_at_delay = float(df.loc[nearest_idx, v_col])

        strict_ok = nearest_dist <= 0.5 * dt
        relaxed_ok = tolerance_s > 0 and nearest_dist <= tolerance_s
        if not (strict_ok or relaxed_ok):
            results.append(ESRResult(
                delay_s=delay,
                esr_ohm=float("nan"),
                v_end_v=vend,
                v_at_delay_v=v_at_delay,
                current_a=abs(i_discharge),
                sample_rate_hz=sample_rate_hz,
                is_approximate=False,
                reason="no sample within window" if strict_mode else "sample too far",
            ))
            continue

        esr = (v_at_delay - vend) / abs(i_discharge) if abs(i_discharge) > 1e-6 else float("nan")
        results.append(ESRResult(
            delay_s=delay,
            esr_ohm=esr,
            v_end_v=vend,
            v_at_delay_v=v_at_delay,
            current_a=abs(i_discharge),
            sample_rate_hz=sample_rate_hz,
            is_approximate=not strict_ok and relaxed_ok,
            reason="",
        ))

    return results
