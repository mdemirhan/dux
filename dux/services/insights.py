from __future__ import annotations

import heapq
from pathlib import Path

from dux.config.schema import AppConfig, PatternRule
from dux.models.enums import InsightCategory
from dux.models.insight import Insight, InsightBundle
from dux.models.scan import ScanNode
from dux.services.patterns import CompiledRuleSet, compile_ruleset, match_all

# Heap entry: (disk_usage, path, Insight).  Using disk usage as the key so the
# smallest item sits at the top of the min-heap for efficient eviction.
type _HeapEntry = tuple[int, str, Insight]


def _heap_push(
    heap: list[_HeapEntry],
    seen: dict[str, int],
    insight: Insight,
    max_size: int,
) -> None:
    """Push *insight* into a bounded min-heap, deduplicating by path."""
    prev_usage = seen.get(insight.path)
    if prev_usage is not None:
        if insight.disk_usage <= prev_usage:
            return
        # Remove old entry lazily — mark it and let eviction clean up.
        # For simplicity we just allow duplicates in the heap and let the
        # final extraction phase deduplicate.
    seen[insight.path] = insight.disk_usage
    entry: _HeapEntry = (insight.disk_usage, insight.path, insight)
    if len(heap) < max_size:
        heapq.heappush(heap, entry)
    elif insight.disk_usage > heap[0][0]:
        heapq.heapreplace(heap, entry)


def generate_insights(root: ScanNode, config: AppConfig) -> InsightBundle:
    # --- build additional path rules ---
    additional_paths: list[tuple[str, PatternRule]] = []
    for category, sources in (
        (InsightCategory.TEMP, config.additional_temp_paths),
        (InsightCategory.CACHE, config.additional_cache_paths),
    ):
        for raw_base in sources:
            base = str(Path(raw_base).expanduser()).rstrip("/")
            additional_paths.append(
                (
                    base,
                    PatternRule(
                        name=f"Additional {category.value} path",
                        pattern=base,
                        category=category,
                        apply_to="both",
                        stop_recursion=False,
                    ),
                )
            )

    # --- compile all rules into a single dispatch structure ---
    ruleset: CompiledRuleSet = compile_ruleset(
        [
            config.temp_patterns,
            config.cache_patterns,
            config.build_artifact_patterns,
        ],
        additional_paths=additional_paths or None,
    )

    # --- per-category min-heaps ---
    heaps: dict[InsightCategory, list[_HeapEntry]] = {cat: [] for cat in InsightCategory}
    seen: dict[InsightCategory, dict[str, int]] = {cat: {} for cat in InsightCategory}

    # --- aggregate counters (track *all* matches, not just top-K) ---
    category_counts: dict[InsightCategory, int] = {}
    category_disk_usage: dict[InsightCategory, int] = {}
    category_paths: dict[InsightCategory, set[str]] = {cat: set() for cat in InsightCategory}

    def _record(insight: Insight) -> None:
        cat = insight.category
        category_counts[cat] = category_counts.get(cat, 0) + 1
        category_disk_usage[cat] = category_disk_usage.get(cat, 0) + insight.disk_usage
        category_paths[cat].add(insight.path)
        _heap_push(heaps[cat], seen[cat], insight, config.max_insights_per_category)

    # --- main traversal ---
    _TEMP = InsightCategory.TEMP
    _CACHE = InsightCategory.CACHE
    _temp_cache = {_TEMP.value, _CACHE.value}

    stack: list[tuple[ScanNode, bool]] = [(root, False)]
    while stack:
        node, in_temp_or_cache = stack.pop()

        # Early skip: descendants of already-matched temp/cache dirs need no
        # processing — the parent insight already has the aggregate size.
        if in_temp_or_cache:
            continue

        path = node.path
        basename = node.name
        is_dir = node.is_dir

        # Lowercase once per entry for case-insensitive pattern matching.
        lpath = path.lower()
        lbase = basename.lower()

        # Single-pass match across all categories
        matched_rules = match_all(ruleset, lpath, lbase, is_dir, path)

        local_in_temp_cache = False
        build_rule: PatternRule | None = None
        for rule in matched_rules:
            _record(_insight_from_rule(node, rule))
            if rule.category.value in _temp_cache:
                local_in_temp_cache = True
            if rule.stop_recursion:
                build_rule = rule

        if is_dir:
            if build_rule is not None:
                continue
            for child in reversed(node.children):
                stack.append((child, local_in_temp_cache))

    # --- merge heaps into a single sorted list, deduplicating ---
    all_insights: list[Insight] = []
    final_seen: set[str] = set()
    for cat in InsightCategory:
        # Extract largest-first from each heap.
        entries = sorted(heaps[cat], key=lambda e: e[0], reverse=True)
        for _, path, insight in entries:
            if path not in final_seen:
                final_seen.add(path)
                all_insights.append(insight)

    all_insights.sort(key=lambda x: x.disk_usage, reverse=True)

    return InsightBundle(
        insights=all_insights,
        category_counts=category_counts,
        category_disk_usage=category_disk_usage,
        category_paths=category_paths,
    )


def _insight_from_rule(node: ScanNode, rule: PatternRule) -> Insight:
    return Insight(
        path=node.path,
        size_bytes=node.size_bytes,
        category=rule.category,
        summary=rule.name,
        kind=node.kind,
        disk_usage=node.disk_usage,
    )


def filter_insights(bundle: InsightBundle, categories: set[InsightCategory]) -> list[Insight]:
    return [item for item in bundle.insights if item.category in categories]
