from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ParsedId:
    index_id: str
    entity_type: str  # "cell" or "block" or "unknown"
    date_ymd: Optional[str]  # YYMMDD if available
    group_id: Optional[str]  # G01, G02, B01, Test, etc. for Looker filtering


CELL_RE = re.compile(r"(?P<ymd>\d{6})_(?P<n>\d)_(?P<g>G\d+)_(?P<num>\d{3})")
CELL_RE_ALT = re.compile(r"(?P<ymd>\d{6})_(?P<g>G\d+)_(?P<num>\d{3})")  # 260206_G01_015
BLOCK_RE = re.compile(r"(?P<ymd>\d{6})_(?P<n>\d)_(?P<b>B\d+)_?(?P<num>\d{3})?")
BLOCK_RE_ALT = re.compile(r"(?P<ymd>\d{6})_(?P<b>B\d+)_?(?P<num>\d{3})?")
# Fallback: extract group-like segment (G01, G02, G1, B01, Test, etc.)
GROUP_RE = re.compile(r"_([A-Za-z]+\d*)_")


def parse_index_id(object_id: str, path: str | Path) -> ParsedId:
    text = object_id.strip()
    m_cell = CELL_RE.search(text) or CELL_RE_ALT.search(text)
    if m_cell:
        ymd = m_cell.group("ymd")
        g = m_cell.group("g")
        index_id = f"{ymd}_{g}_{m_cell.group('num')}"
        return ParsedId(index_id=index_id, entity_type="cell", date_ymd=ymd, group_id=g)

    m_block = BLOCK_RE.search(text) or BLOCK_RE_ALT.search(text)
    if m_block:
        ymd = m_block.group("ymd")
        g = m_block.group("b")
        num = m_block.group("num") or "000"
        index_id = f"{ymd}_{g}_{num}"
        return ParsedId(index_id=index_id, entity_type="block", date_ymd=ymd, group_id=g)

    # Fallback: look for YYMMDD and group anywhere
    any_ymd = re.search(r"\d{6}", text)
    ymd = any_ymd.group(0) if any_ymd else None
    any_group = GROUP_RE.search(text)
    group_id = any_group.group(1) if any_group else None
    return ParsedId(
        index_id=text or str(path), entity_type="unknown", date_ymd=ymd, group_id=group_id
    )

