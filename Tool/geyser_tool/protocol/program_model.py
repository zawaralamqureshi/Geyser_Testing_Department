from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class ProgramStep:
    step_num: int
    mode: str
    params: Dict[str, float] = field(default_factory=dict)
    cycle_ref: Optional[Tuple[int, int]] = None  # (target_step, n_times)


@dataclass
class ProgramDefinition:
    preparation: List[ProgramStep] = field(default_factory=list)
    main: List[ProgramStep] = field(default_factory=list)
    completion: List[ProgramStep] = field(default_factory=list)

