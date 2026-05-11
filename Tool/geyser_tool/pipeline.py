from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from geyser_tool.ids import parse_index_id
from geyser_tool.parsers.header import parse_header
from geyser_tool.parsers.clk_reader import read_clk_with_metrics
from geyser_tool.protocol.detector import DetectedProtocol, detect_protocol
from geyser_tool.protocol.program_model import ProgramDefinition, ProgramStep


@dataclass
class FileSummary:
    path: Path
    object_id: str
    index_id: str
    entity_type: str
    period_s: float | None
    protocol: DetectedProtocol
    date_ymd: str | None  # YYMMDD for Looker date filter
    group_id: str | None  # G01, G02, B01, etc. for Looker group filter


def _program_from_header(program_lines: List[str]) -> ProgramDefinition:
    """
    Very simple program parser for now:
    - Use the raw lines as ProgramStep.mode strings.
    - Step numbers are taken from the prefix when present.
    """
    steps: list[ProgramStep] = []
    for raw in program_lines:
        txt = raw.strip()
        if not txt:
            continue
        # Lines look like ' 5 Charge CC 4.5A to 1.7V or 10000s. Period ESR: 1s. Duration ESR: 1000Hz.'
        parts = txt.split(maxsplit=1)
        try:
            step_num = int(parts[0])
            mode = parts[1] if len(parts) > 1 else ""
        except (ValueError, IndexError):
            step_num = -1
            mode = txt
        steps.append(ProgramStep(step_num=step_num, mode=mode))
    return ProgramDefinition(main=steps)


def summarize_clk_files(paths: Iterable[Path]) -> Iterable[FileSummary]:
    for root in paths:
        root = root.resolve()
        if root.is_file() and root.name.endswith("-CLK.txt"):
            clk_files = [root]
        else:
            clk_files = list(root.rglob("*-CLK.txt"))
        for clk in clk_files:
            hdr = parse_header(clk)
            parsed = parse_index_id(hdr.object_id or "", clk)
            prog_def = _program_from_header(hdr.program_lines)
            proto = detect_protocol(prog_def)
            # Trigger metrics parsing to ensure the file is well-formed; result will
            # be used later when wiring to BigQuery.
            _steps, _cycles = read_clk_with_metrics(clk)
            yield FileSummary(
                path=clk,
                object_id=hdr.object_id,
                index_id=parsed.index_id,
                entity_type=parsed.entity_type,
                period_s=hdr.data_period_s,
                protocol=proto,
                date_ymd=parsed.date_ymd,
                group_id=parsed.group_id,
            )

