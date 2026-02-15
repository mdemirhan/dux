from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from result import Result

from diskanalysis.models.enums import NodeKind


ProgressCallback = Callable[[str, int, int], None]
CancelCheck = Callable[[], bool]


@dataclass(slots=True)
class ScanNode:
    path: str
    name: str
    kind: NodeKind
    size_bytes: int
    modified_ts: float
    children: list[ScanNode] = field(default_factory=list)

    @property
    def is_dir(self) -> bool:
        return self.kind is NodeKind.DIRECTORY


@dataclass(slots=True)
class ScanStats:
    files: int = 0
    directories: int = 0
    access_errors: int = 0


@dataclass(slots=True)
class ScanOptions:
    max_depth: int | None = None


@dataclass(slots=True, frozen=True)
class ScanSnapshot:
    root: ScanNode
    stats: ScanStats


class ScanErrorCode(str, Enum):
    NOT_FOUND = "not_found"
    NOT_DIRECTORY = "not_directory"
    ROOT_STAT_FAILED = "root_stat_failed"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


@dataclass(slots=True, frozen=True)
class ScanError:
    code: ScanErrorCode
    path: str
    message: str


ScanResult = Result[ScanSnapshot, ScanError]


def norm_sep(path: str) -> str:
    return path.replace("\\", "/")
