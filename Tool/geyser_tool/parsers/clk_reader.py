from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

import pandas as pd


def read_clk_table(path: str | Path) -> pd.DataFrame:
    """
    Read the CLK summary table (per-step and GNRL rows) into a DataFrame.

    This skips the header lines until the line starting with 'Cycle  Step'
    (or with leading spaces) and then parses the remaining space-separated
    table using pandas. Returns empty DataFrame if no data table is found
    (e.g. header-only or incomplete CLK files).
    """
    p = Path(path)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()

    start_idx = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("Cycle") and "Step" in line:
            start_idx = i
            break
    if start_idx is None:
        return pd.DataFrame()

    table_text = "\n".join(lines[start_idx:])
    df = pd.read_fwf(pd.io.common.StringIO(table_text), widths=None, infer_nrows=50)
    # Normalize column names if present
    df.columns = [str(c).strip() for c in df.columns]
    # Drop completely empty rows
    df = df.dropna(how="all")
    return df


def split_cycles(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split a CLK DataFrame into per-step rows and GNRL (per-cycle) rows.
    """
    step_col = next((c for c in df.columns if c.lower().startswith("step")), None)
    if step_col is None:
        return df, df.iloc[0:0]
    mask_gnrl = df[step_col].astype(str).str.contains("GNRL", na=False)
    gnrl = df[mask_gnrl].copy()
    steps = df[~mask_gnrl].copy()
    return steps, gnrl


STEP_TYPE_MAP: Dict[str, str] = {
    # charge modes
    "CCC": "CHARGE_CC",
    "CHCC": "CHARGE_CC",
    "CHCP": "CHARGE_CP",
    "CHCV": "CHARGE_CV",
    # discharge modes
    "DCC": "DISCHARGE_CC",
    "DCCC": "DISCHARGE_CC",
    "DCHCC": "DISCHARGE_CC",
    "DCP": "DISCHARGE_CP",
    "DCHCP": "DISCHARGE_CP",
    "DCV": "DISCHARGE_CV",
    "DCHCV": "DISCHARGE_CV",
    "DCR": "DISCHARGE_CR",
    "DCHCR": "DISCHARGE_CR",
    # scans
    "SNU": "SCAN_V",
    "SNI": "SCAN_I",
    "SNP": "SCAN_P",
    "SNR": "SCAN_R",
    # relax / logger
    "RLX": "RELAX",
    "RLAX": "RELAX",
    "LGU": "LOGGER_V",
    # tables
    "TBU": "TABLE_V",
    "TBI": "TABLE_I",
    "TBP": "TABLE_P",
    "TBLP": "TABLE_P",
    "TBR": "TABLE_R",
    # pulses
    "IPI": "PULSE_I",
    "IPU": "PULSE_V",
    "IPP": "PULSE_P",
    "IPR": "PULSE_R",
    # mppt
    "MPPT": "MPPT",
}


def add_step_fields(df_steps: pd.DataFrame) -> pd.DataFrame:
    """
    From a per-step CLK DataFrame, derive numeric step_no, raw step marker,
    and canonical step_type.
    """
    step_col = next((c for c in df_steps.columns if c.lower().startswith("step")), None)
    if step_col is None:
        return df_steps

    if len(df_steps) == 0:
        return df_steps

    # Extract numeric part and text marker, e.g. '5CCC' -> step_no=5, marker='CCC'
    raw = df_steps[step_col].astype(str)

    def parse_marker(val: str) -> Tuple[int, str]:
        s = val.strip()
        num = ""
        for ch in s:
            if ch.isdigit():
                num += ch
            else:
                break
        marker = s[len(num) :] if num else s
        try:
            n = int(num) if num else -1
        except ValueError:
            n = -1
        return n, marker

    parsed = raw.map(parse_marker)
    df_steps = df_steps.copy()
    df_steps["step_no"], df_steps["step_marker"] = zip(*parsed)

    def to_type(marker: str) -> str:
        m = marker.upper()
        return STEP_TYPE_MAP.get(m, m or "UNKNOWN")

    df_steps["step_type"] = df_steps["step_marker"].astype(str).map(to_type)
    return df_steps


def read_clk_with_metrics(path: str | Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Convenience: read a CLK file into (step_metrics, cycle_metrics) DataFrames
    with derived step fields.
    """
    df = read_clk_table(path)
    steps, gnrl = split_cycles(df)
    steps = add_step_fields(steps)
    return steps, gnrl

