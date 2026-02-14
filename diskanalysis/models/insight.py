from __future__ import annotations

from dataclasses import dataclass, field

from diskanalysis.models.enums import InsightCategory


@dataclass(slots=True)
class Insight:
    path: str
    size_bytes: int
    category: InsightCategory
    safe_to_delete: bool
    summary: str
    recommendation: str
    modified_ts: float


@dataclass(slots=True)
class InsightBundle:
    insights: list[Insight]
    category_counts: dict[InsightCategory, int] = field(default_factory=dict)
    category_sizes: dict[InsightCategory, int] = field(default_factory=dict)
    category_paths: dict[InsightCategory, set[str]] = field(default_factory=dict)
