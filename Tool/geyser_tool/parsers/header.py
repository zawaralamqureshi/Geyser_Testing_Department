from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class ProgramStepHeader:
    raw_line: str


@dataclass
class FileHeader:
    analyzer: str
    object_id: str
    program_lines: List[str] = field(default_factory=list)
    preparation_lines: List[str] = field(default_factory=list)
    completion_lines: List[str] = field(default_factory=list)
    limitations: str | None = None
    data_period_s: Optional[float] = None
    started_raw: str | None = None
    is_clk: bool = False


def parse_header(path: str | Path) -> FileHeader:
    """
    Parse the textual header of a CLK or RAW result file.

    Stops when it reaches the tabular header line starting with
    'Cycle  Step' (CLK) or similar.
    """
    p = Path(path)
    analyzer = ""
    object_id = ""
    program_lines: list[str] = []
    preparation_lines: list[str] = []
    completion_lines: list[str] = []
    limitations = None
    period_s: float | None = None
    started_raw: str | None = None
    is_clk = False

    mode: str | None = None  # "program", "prep", "completion"

    with p.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.rstrip("\n")
            if stripped.startswith("  Cycle") or stripped.startswith("   Cycle"):
                # We have reached the table header for CLK
                is_clk = True
                break

            if stripped.startswith("Analyzer:"):
                analyzer = stripped.split("Analyzer:", 1)[1].strip()
                continue
            if stripped.startswith("Analyzer"):
                # RAW/other variants
                analyzer = stripped.split("Analyzer", 1)[1].strip()
            if stripped.startswith("Object:"):
                object_id = stripped.split("Object:", 1)[1].strip()
                continue

            if stripped.startswith("Preparation:"):
                mode = "prep"
                continue
            if stripped.startswith("Program:"):
                mode = "program"
                continue
            if stripped.startswith("Completion:"):
                mode = "completion"
                continue

            if stripped.startswith("Limitation"):
                limitations = stripped
                continue
            if stripped.startswith("Data recording period"):
                # e.g. 'Data recording period: 10Hz.' or '0.1s.'
                part = stripped.split(":", 1)[1].strip().rstrip(".")
                if part.endswith("Hz"):
                    try:
                        hz = float(part.replace("Hz", "").strip())
                        period_s = 1.0 / hz if hz > 0 else None
                    except ValueError:
                        period_s = None
                elif part.endswith("s"):
                    try:
                        period_s = float(part.replace("s", "").strip())
                    except ValueError:
                        period_s = None
                continue

            if "Testing started" in stripped or "started:" in stripped:
                started_raw = stripped
                continue

            if mode == "program" and stripped.strip():
                program_lines.append(stripped)
            elif mode == "prep" and stripped.strip():
                preparation_lines.append(stripped)
            elif mode == "completion" and stripped.strip():
                completion_lines.append(stripped)

    return FileHeader(
        analyzer=analyzer,
        object_id=object_id,
        program_lines=program_lines,
        preparation_lines=preparation_lines,
        completion_lines=completion_lines,
        limitations=limitations,
        data_period_s=period_s,
        started_raw=started_raw,
        is_clk=is_clk,
    )

