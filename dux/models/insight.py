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
class InsightBundle:
    insights: list[Insight]
    category_counts: dict[InsightCategory, int] = field(default_factory=dict)
    category_size_bytes: dict[InsightCategory, int] = field(default_factory=dict)
    category_disk_usage: dict[InsightCategory, int] = field(default_factory=dict)
    category_paths: dict[InsightCategory, set[str]] = field(default_factory=dict)
