"""
Protocol detection: match parsed program against YAML registry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .program_model import ProgramDefinition


@dataclass
class DetectedProtocol:
    label: str
    confidence: float


# Block cyclability CLK text uses typo "Discarge CC"; cells use correct "Discharge CC".
_BLOCK_DISCHARGE_MARKER = "DISCARGE CC"
_CELL_DISCHARGE_MARKER = "DISCHARGE CC"


def _load_registry() -> List[Dict[str, Any]]:
    """Load protocol registry from YAML."""
    registry_path = Path(__file__).parent / "registry.yaml"
    if not registry_path.exists():
        return []
    with registry_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("protocols", [])


def _joined_program_modes(prog: ProgramDefinition) -> str:
    return " ".join(s.mode for s in prog.main)


def _cyclability_above_20_cycles(prog: ProgramDefinition, main_upper: str) -> Optional[DetectedProtocol]:
    """
    SOP 5C-style long cyclability (protocol label CYCLABILITY).

    Cells: "Cycle to step <n>   <count> times" with count > 20 and discharge step "Discharge CC".
    Blocks: "Preset number of cycles: <N>" with N > 20 and "Discarge CC" (typo in CLK).
    """
    if len(prog.main) < 2:
        return None

    text = _joined_program_modes(prog)
    tu = main_upper

    if "CHARGE CC" not in tu:
        return None

    m_cell = re.search(
        r"cycle\s+to\s+step\s+\d+\s+(\d+)\s+times",
        text,
        flags=re.IGNORECASE,
    )
    m_block = re.search(
        r"preset\s+number\s+of\s+cycles\s*:\s*(\d+)",
        text,
        flags=re.IGNORECASE,
    )

    cell_count = int(m_cell.group(1)) if m_cell else None
    block_count = int(m_block.group(1)) if m_block else None

    cell_eligible = (
        cell_count is not None
        and cell_count > 20
        and _CELL_DISCHARGE_MARKER in tu
        and _BLOCK_DISCHARGE_MARKER not in tu
    )
    block_eligible = (
        block_count is not None
        and block_count > 20
        and _BLOCK_DISCHARGE_MARKER in tu
    )

    if cell_eligible:
        return DetectedProtocol(label="CYCLABILITY", confidence=0.5)
    if block_eligible:
        return DetectedProtocol(label="CYCLABILITY", confidence=0.5)
    return None


def detect_protocol(prog: ProgramDefinition) -> DetectedProtocol:
    """
    CYCLABILITY (long cyclability, > 20 cycles) is evaluated first, then the YAML
    registry in file order. That way high preset-count block runs are not labeled
    BLOCK_CYCLING when they qualify as CYCLABILITY.

    Falls back to heuristics if registry is empty or no pattern matches.
    """
    main_modes: List[str] = [s.mode for s in prog.main]
    main_upper = " ".join(main_modes).upper()

    cy = _cyclability_above_20_cycles(prog, main_upper)
    if cy is not None:
        return cy

    for entry in _load_registry():
        label = entry.get("label", "")
        confidence = float(entry.get("confidence", 0.5))
        keywords_any = entry.get("keywords_any") or []
        keywords_all = entry.get("keywords_all") or []
        min_steps = entry.get("min_steps")

        if min_steps is not None and len(prog.main) < min_steps:
            continue
        if keywords_all and not all(k.upper() in main_upper for k in keywords_all):
            continue
        if keywords_any and not any(k.upper() in main_upper for k in keywords_any):
            continue
        return DetectedProtocol(label=label, confidence=confidence)

    # Fallback heuristics (e.g. missing or empty registry)
    if (
        "SCANNING U" in main_upper
        and "CHARGE CC" in main_upper
        and "DISCHARGE CC" in main_upper
        and "CHARGE CV" in main_upper
        and ("REST" in main_upper or "LOGGER U" in main_upper)
    ):
        return DetectedProtocol(label="STANDARD_CELL", confidence=0.7)
    if "SCANNING U" in main_upper and "PRESET NUMBER OF CYCLES" in main_upper:
        return DetectedProtocol(label="BLOCK_SCANNING", confidence=0.7)
    if (
        _BLOCK_DISCHARGE_MARKER in main_upper
        and "REST" in main_upper
        and "PRESET NUMBER OF CYCLES" in main_upper
    ):
        return DetectedProtocol(label="BLOCK_CYCLING", confidence=0.7)
    return DetectedProtocol(label="UNKNOWN", confidence=0.0)
