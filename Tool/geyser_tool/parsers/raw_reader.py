"""
RAW file reader for time-series data (time, U, I, T, Q, E per sample).

**Per-step analyzer clock:** Within a single RAW export, ``Time,s`` resets to ``0``
whenever the analyzer moves to a **new logged step**. The RAW ``Step`` column (e.g. ``6DCCC``)
identifies the segment; contiguous rows sharing the exact same ``Step`` string belong to the
same step instance.

``add_continuous_time`` builds ``time_continuous_s`` = cumulative offset plus local ``Time,s``:

- Whenever ``Step`` **changes** between successive rows, a new segment begins; the offset
  used for subsequent rows jumps forward by ``max(Time,s)`` measured **within the previous segment**.
  That reproduces total elapsed duration across steps **as recorded by the RAW file** — e.g.
  a 52.4 s Charge CC contributes 52.4 s to the within-file continuum.

Mapped fields:

- ``step_no`` — integer prefix of ``Step`` (``7`` from ``7RLAX``).
- ``step_marker`` — remainder (``RLAX``, ``DCCC``, …).
- ``step_type`` — canonical bucket via ``STEP_TYPE_MAP`` matching ``clk_reader``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import pandas as pd

from geyser_tool.parsers.clk_reader import STEP_TYPE_MAP


def _find_raw_table_start(lines: List[str]) -> Optional[int]:
    """Find the line index where the RAW table header starts (Cycle, Step, Time,s...)."""
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if "Cycle" in stripped and "Step" in stripped and ("Time" in stripped or "Time,s" in stripped):
            return i
    return None


def _is_footer_line(line: str) -> bool:
    """Detect footer/summary lines (e.g. 'Aborted by user: 11/02/2026') to stop table parse."""
    s = line.strip()
    if not s:
        return False
    if "Aborted" in s or "Completion" in s or "Started" in s or "Limitation" in s:
        return True
    if s.startswith("Data recording") or s.startswith("Preset number"):
        return True
    return False


def read_raw_table(path: str | Path) -> pd.DataFrame:
    """
    Read the RAW time-series table into a DataFrame.
    Columns: Cycle, Step, Time,s, U,V, I,A, T,°C, ESR,Ohm, Q,Ah, E,Wh (or variants).
    Footer lines (e.g. 'Aborted by user: 11/02/2026') are excluded to avoid date strings in Time,s.
    """
    p = Path(path)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()

    start_idx = _find_raw_table_start(lines)
    if start_idx is None:
        raise ValueError(f"Could not find RAW table header in {p}")

    data_lines = [lines[start_idx]]  # header
    for line in lines[start_idx + 1 :]:
        if _is_footer_line(line):
            break
        data_lines.append(line)

    table_text = "\n".join(data_lines)
    df = pd.read_fwf(pd.io.common.StringIO(table_text), widths=None, infer_nrows=100)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")
    return df


def _parse_step_marker(step_val: str) -> Tuple[int, str]:
    """Extract step_no and marker from step string, e.g. '1DCCC' -> (1, 'DCCC')."""
    s = str(step_val).strip()
    num = ""
    for ch in s:
        if ch.isdigit():
            num += ch
        else:
            break
    marker = s[len(num):] if num else s
    try:
        n = int(num) if num else -1
    except ValueError:
        n = -1
    return n, marker


def _to_step_type(marker: str) -> str:
    m = marker.upper()
    return STEP_TYPE_MAP.get(m, m or "UNKNOWN")


def add_continuous_time(df: pd.DataFrame) -> pd.DataFrame:
    """
    Concatenate analyzer ``Time,s`` across **Step** transitions within one RAW export.

    A new timeline segment begins whenever the ``Step`` cell differs from the previous row.
    Each segment retains the RAW ``Time,s`` ramp (0 … duration-of-step).

    Implemented by grouping contiguous runs of identical ``Step`` strings; cumulative offset before
    each run equals sum of ``max(Time,s)`` observed in earlier runs --- matching per-step durations
    in the RAW file (subject to parsing quality of the RAW table rows).
    """
    step_col = next((c for c in df.columns if c.lower().startswith("step")), None)
    time_col = next(
        (c for c in df.columns if "time" in c.lower() and ("s" in c.lower() or "," in c)),
        None,
    )
    if step_col is None or time_col is None:
        df = df.copy()
        df["time_continuous_s"] = df.get(time_col, pd.Series(dtype=float))
        return df

    df = df.copy()
    df["_step_key"] = df[step_col].astype(str)
    df["_step_group"] = (df["_step_key"] != df["_step_key"].shift()).cumsum()

    # Per-step max time (duration of that step)
    step_durations = df.groupby("_step_group")[time_col].max()
    # Cumulative offset: before step N, we've had sum of durations of steps 0..N-1
    offsets = step_durations.cumsum().shift(1, fill_value=0)
    df["time_offset_s"] = df["_step_group"].map(offsets)
    df["time_continuous_s"] = df["time_offset_s"] + df[time_col].astype(float)
    df = df.drop(columns=["_step_key", "_step_group", "time_offset_s"])
    return df


def read_raw_with_continuous_time(path: str | Path) -> pd.DataFrame:
    """
    Read RAW file and return DataFrame with:
    - All original columns (Cycle, Step, Time,s, U,V, I,A, T,°C, Q,Ah, E,Wh, etc.)
    - step_no, step_marker, step_type (derived from Step)
    - time_continuous_s (time concatenated across steps for full program timeline)
    """
    df = read_raw_table(path)
    step_col = next((c for c in df.columns if c.lower().startswith("step")), None)
    if step_col:
        parsed = df[step_col].astype(str).map(_parse_step_marker)
        df["step_no"] = [p[0] for p in parsed]
        df["step_marker"] = [p[1] for p in parsed]
        df["step_type"] = df["step_marker"].map(_to_step_type)
    df = add_continuous_time(df)
    return df


def find_raw_files_for_clk(clk_path: Path) -> List[Path]:
    """
    Find RAW files associated with a CLK file.
    Supports two folder layouts:
    - Blocks (ACK75.48): RAW subfolder, e.g. .../001/RAW/{object_id}-00000000.txt
    - Cells (ACK75.10): {object_id}-RAW folder, e.g. .../001/{object_id}-RAW/{object_id}-00000001.txt
    Also checks parent dirs (up to 4 levels) and sibling RAW folder.
    """
    clk_path = Path(clk_path).resolve()
    if not clk_path.name.endswith("-CLK.txt"):
        return []
    object_id = clk_path.stem.replace("-CLK", "")

    candidates: List[Path] = []
    current = clk_path.parent
    for _ in range(5):
        # Blocks format: RAW subfolder
        raw_dir = current / "RAW"
        if raw_dir.is_dir():
            candidates.extend(raw_dir.glob(f"{object_id}-*.txt"))
        # Cells format: {object_id}-RAW folder (e.g. 260203_1_G01_033_C_001-RAW)
        cells_raw_dir = current / f"{object_id}-RAW"
        if cells_raw_dir.is_dir():
            candidates.extend(cells_raw_dir.glob(f"{object_id}-*.txt"))
        current = current.parent
        if not current or current == current.parent:
            break
    # Sibling folder named RAW (Blocks)
    for sibling in clk_path.parent.iterdir():
        if sibling.is_dir() and sibling.name.upper() == "RAW":
            candidates.extend(sibling.glob(f"{object_id}-*.txt"))
            break
        if sibling.is_dir() and sibling.name == f"{object_id}-RAW":
            candidates.extend(sibling.glob(f"{object_id}-*.txt"))
            break

    return sorted(set(candidates))


def read_all_raw_for_clk(clk_path: Path) -> Iterator[Tuple[Path, pd.DataFrame]]:
    """
    Yield (raw_path, dataframe) for each RAW file associated with the CLK.
    Each DataFrame has time_continuous_s; note that time is local to each file (cycle).
    To get global continuous time across all cycles, caller must add cycle-based offset.
    """
    for raw_path in find_raw_files_for_clk(clk_path):
        try:
            df = read_raw_with_continuous_time(raw_path)
            yield raw_path, df
        except Exception:
            continue
