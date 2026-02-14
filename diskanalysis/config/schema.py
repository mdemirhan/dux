from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from diskanalysis.models.enums import InsightCategory


@dataclass(slots=True)
class PatternRule:
    name: str
    pattern: str
    category: InsightCategory
    apply_to: Literal["file", "dir", "both"] = "both"
    stop_recursion: bool = False


@dataclass(slots=True)
class AppConfig:
    additional_temp_paths: list[str] = field(default_factory=list)
    additional_cache_paths: list[str] = field(default_factory=list)
    temp_patterns: list[PatternRule] = field(default_factory=list)
    cache_patterns: list[PatternRule] = field(default_factory=list)
    build_artifact_patterns: list[PatternRule] = field(default_factory=list)
    follow_symlinks: bool = False
    max_depth: int | None = None
    scan_workers: int = 4
    summary_top_count: int = 15
    page_size: int = 100
    max_insights_per_category: int = 1000
    overview_top_folders: int = 100
    scroll_step: int = 10

    def to_dict(self) -> dict[str, Any]:
        return {
            "additionalTempPaths": self.additional_temp_paths,
            "additionalCachePaths": self.additional_cache_paths,
            "followSymlinks": self.follow_symlinks,
            "maxDepth": self.max_depth,
            "scanWorkers": self.scan_workers,
            "summaryTopCount": self.summary_top_count,
            "pageSize": self.page_size,
            "maxInsightsPerCategory": self.max_insights_per_category,
            "overviewTopFolders": self.overview_top_folders,
            "scrollStep": self.scroll_step,
            "tempPatterns": [_rule_to_dict(rule) for rule in self.temp_patterns],
            "cachePatterns": [_rule_to_dict(rule) for rule in self.cache_patterns],
            "buildArtifactPatterns": [
                _rule_to_dict(rule) for rule in self.build_artifact_patterns
            ],
        }


def _rule_to_dict(rule: PatternRule) -> dict[str, Any]:
    return {
        "name": rule.name,
        "pattern": rule.pattern,
        "category": rule.category.value,
        "applyTo": rule.apply_to,
        "stopRecursion": rule.stop_recursion,
    }


_VALID_APPLY_TO: set[str] = {"file", "dir", "both"}


def _parse_apply_to(value: Any) -> Literal["file", "dir", "both"]:
    raw = str(value)
    if raw in _VALID_APPLY_TO:
        return raw  # type: ignore[return-value]
    return "both"


def _rule_from_dict(payload: dict[str, Any]) -> PatternRule:
    return PatternRule(
        name=str(payload["name"]),
        pattern=str(payload["pattern"]),
        category=InsightCategory(str(payload["category"])),
        apply_to=_parse_apply_to(payload.get("applyTo", "both")),
        stop_recursion=bool(payload.get("stopRecursion", False)),
    )


def from_dict(data: dict[str, Any], defaults: AppConfig) -> AppConfig:
    max_depth_raw = data.get("maxDepth", defaults.max_depth)

    return AppConfig(
        additional_temp_paths=[
            str(x)
            for x in data.get("additionalTempPaths", defaults.additional_temp_paths)
        ],
        additional_cache_paths=[
            str(x)
            for x in data.get("additionalCachePaths", defaults.additional_cache_paths)
        ],
        follow_symlinks=bool(data.get("followSymlinks", defaults.follow_symlinks)),
        max_depth=int(max_depth_raw) if max_depth_raw is not None else None,
        scan_workers=max(1, int(data.get("scanWorkers", defaults.scan_workers))),
        summary_top_count=max(
            1, int(data.get("summaryTopCount", defaults.summary_top_count))
        ),
        page_size=max(10, int(data.get("pageSize", defaults.page_size))),
        max_insights_per_category=max(
            10,
            int(data.get("maxInsightsPerCategory", defaults.max_insights_per_category)),
        ),
        overview_top_folders=max(
            5, int(data.get("overviewTopFolders", defaults.overview_top_folders))
        ),
        scroll_step=max(1, int(data.get("scrollStep", defaults.scroll_step))),
        temp_patterns=[_rule_from_dict(x) for x in data["tempPatterns"]]
        if "tempPatterns" in data
        else list(defaults.temp_patterns),
        cache_patterns=[_rule_from_dict(x) for x in data["cachePatterns"]]
        if "cachePatterns" in data
        else list(defaults.cache_patterns),
        build_artifact_patterns=[
            _rule_from_dict(x) for x in data["buildArtifactPatterns"]
        ]
        if "buildArtifactPatterns" in data
        else list(defaults.build_artifact_patterns),
    )
