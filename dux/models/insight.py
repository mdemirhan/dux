from __future__ import annotations

from dataclasses import dataclass, field

from dux.models.enums import InsightCategory, NodeKind


@dataclass(slots=True)
class Insight:
    path: str
    size_bytes: int
    category: InsightCategory
    summary: str
    kind: NodeKind = NodeKind.FILE
    disk_usage: int = 0


@dataclass(slots=True)
class CategoryStats:
    count: int = 0
    size_bytes: int = 0
    disk_usage: int = 0
    paths: set[str] = field(default_factory=set)


@dataclass(slots=True)
class InsightBundle:
    insights: list[Insight]
    by_category: dict[InsightCategory, CategoryStats] = field(default_factory=dict)
