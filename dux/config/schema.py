from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dux.models.enums import ApplyTo, InsightCategory

_APPLY_TO_FROM_STR: dict[str, ApplyTo] = {
    "file": ApplyTo.FILE,
    "dir": ApplyTo.DIR,
    "both": ApplyTo.BOTH,
}

_APPLY_TO_TO_STR: dict[ApplyTo, str] = {v: k for k, v in _APPLY_TO_FROM_STR.items()}


@dataclass(slots=True)
class PatternRule:
    name: str
    pattern: str
    category: InsightCategory
    apply_to: ApplyTo = ApplyTo.BOTH
    stop_recursion: bool = False


@dataclass(slots=True)
class AppConfig:
    additional_temp_paths: list[str] = field(default_factory=list)
    additional_cache_paths: list[str] = field(default_factory=list)
    temp_patterns: list[PatternRule] = field(default_factory=list)
    cache_patterns: list[PatternRule] = field(default_factory=list)
    build_artifact_patterns: list[PatternRule] = field(default_factory=list)
    max_depth: int | None = None
    scan_workers: int = 4
    top_count: int = 15
    page_size: int = 100
    max_insights_per_category: int = 1000
    overview_top_dirs: int = 100
    scroll_step: int = 10

    def to_dict(self) -> dict[str, Any]:
        return {
            "additionalTempPaths": self.additional_temp_paths,
            "additionalCachePaths": self.additional_cache_paths,
            "maxDepth": self.max_depth,
            "scanWorkers": self.scan_workers,
            "topCount": self.top_count,
            "pageSize": self.page_size,
            "maxInsightsPerCategory": self.max_insights_per_category,
            "overviewTopDirs": self.overview_top_dirs,
            "scrollStep": self.scroll_step,
            "tempPatterns": [_rule_to_dict(rule) for rule in self.temp_patterns],
            "cachePatterns": [_rule_to_dict(rule) for rule in self.cache_patterns],
            "buildArtifactPatterns": [_rule_to_dict(rule) for rule in self.build_artifact_patterns],
        }


def _rule_to_dict(rule: PatternRule) -> dict[str, Any]:
    return {
        "name": rule.name,
        "pattern": rule.pattern,
        "category": rule.category.value,
        "applyTo": _APPLY_TO_TO_STR.get(rule.apply_to, "both"),
        "stopRecursion": rule.stop_recursion,
    }


def _parse_apply_to(value: Any) -> ApplyTo:
    return _APPLY_TO_FROM_STR.get(str(value), ApplyTo.BOTH)


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
        additional_temp_paths=[str(x) for x in data.get("additionalTempPaths", defaults.additional_temp_paths)],
        additional_cache_paths=[str(x) for x in data.get("additionalCachePaths", defaults.additional_cache_paths)],
        max_depth=int(max_depth_raw) if max_depth_raw is not None else None,
        scan_workers=max(1, int(data.get("scanWorkers", defaults.scan_workers))),
        top_count=max(1, int(data.get("topCount", defaults.top_count))),
        page_size=max(10, int(data.get("pageSize", defaults.page_size))),
        max_insights_per_category=max(
            10,
            int(data.get("maxInsightsPerCategory", defaults.max_insights_per_category)),
        ),
        overview_top_dirs=max(5, int(data.get("overviewTopDirs", defaults.overview_top_dirs))),
        scroll_step=max(1, int(data.get("scrollStep", defaults.scroll_step))),
        temp_patterns=[_rule_from_dict(x) for x in data["tempPatterns"]]
        if "tempPatterns" in data
        else list(defaults.temp_patterns),
        cache_patterns=[_rule_from_dict(x) for x in data["cachePatterns"]]
        if "cachePatterns" in data
        else list(defaults.cache_patterns),
        build_artifact_patterns=[_rule_from_dict(x) for x in data["buildArtifactPatterns"]]
        if "buildArtifactPatterns" in data
        else list(defaults.build_artifact_patterns),
    )
