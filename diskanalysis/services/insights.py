from __future__ import annotations

import heapq
from pathlib import Path

from diskanalysis.config.schema import AppConfig, PatternRule
from diskanalysis.models.enums import InsightCategory
from diskanalysis.models.insight import Insight, InsightBundle
from diskanalysis.models.scan import ScanNode, norm_sep
from diskanalysis.services.patterns import CompiledRuleSet, compile_ruleset, match_all

# Heap entry: (size_bytes, path, Insight).  Using size as the key so the
# smallest item sits at the top of the min-heap for efficient eviction.
type _HeapEntry = tuple[int, str, Insight]


def _heap_push(
    heap: list[_HeapEntry],
    seen: dict[str, int],
    insight: Insight,
    max_size: int,
) -> None:
    """Push *insight* into a bounded min-heap, deduplicating by path."""
    prev_size = seen.get(insight.path)
    if prev_size is not None:
        if insight.size_bytes <= prev_size:
            return
        # Remove old entry lazily — mark it and let eviction clean up.
        # For simplicity we just allow duplicates in the heap and let the
        # final extraction phase deduplicate.
    seen[insight.path] = insight.size_bytes
    entry: _HeapEntry = (insight.size_bytes, insight.path, insight)
    if len(heap) < max_size:
        heapq.heappush(heap, entry)
    elif insight.size_bytes > heap[0][0]:
        heapq.heapreplace(heap, entry)


def generate_insights(root: ScanNode, config: AppConfig) -> InsightBundle:
    # --- build additional path rules ---
    additional_paths: list[tuple[str, PatternRule]] = []
    for category, sources in (
        (InsightCategory.TEMP, config.additional_temp_paths),
        (InsightCategory.CACHE, config.additional_cache_paths),
    ):
        for raw_base in sources:
            base = norm_sep(str(Path(raw_base).expanduser())).rstrip("/")
            additional_paths.append(
                (
                    base,
                    PatternRule(
                        name=f"Additional {category.value} path",
                        pattern=base,
                        category=category,
                        safe_to_delete=category is InsightCategory.TEMP,
                        recommendation="Review configured path and clean safely.",
                        apply_to="both",
                        stop_recursion=False,
                    ),
                )
            )

    # --- compile all rules into a single dispatch structure ---
    ruleset: CompiledRuleSet = compile_ruleset(
        [
            (config.temp_patterns, "temp"),
            (config.cache_patterns, "cache"),
            (config.build_artifact_patterns, "build"),
            (config.custom_patterns, "custom"),
        ],
        additional_paths=additional_paths or None,
    )

    # --- per-category min-heaps ---
    heaps: dict[InsightCategory, list[_HeapEntry]] = {
        cat: [] for cat in InsightCategory
    }
    seen: dict[InsightCategory, dict[str, int]] = {cat: {} for cat in InsightCategory}

    # --- aggregate counters (track *all* matches, not just top-K) ---
    category_counts: dict[InsightCategory, int] = {}
    category_sizes: dict[InsightCategory, int] = {}
    category_paths: dict[InsightCategory, set[str]] = {
        cat: set() for cat in InsightCategory
    }

    def _record(insight: Insight) -> None:
        cat = insight.category
        category_counts[cat] = category_counts.get(cat, 0) + 1
        category_sizes[cat] = category_sizes.get(cat, 0) + insight.size_bytes
        category_paths[cat].add(insight.path)
        _heap_push(heaps[cat], seen[cat], insight, config.max_insights_per_category)

    # --- thresholds ---
    large_file_bytes = config.thresholds.large_file_bytes
    large_dir_bytes = config.thresholds.large_dir_bytes

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

        if not local_in_temp_cache:
            if not is_dir and node.size_bytes >= large_file_bytes:
                _record(
                    Insight(
                        path=path,
                        size_bytes=node.size_bytes,
                        category=InsightCategory.LARGE_FILE,
                        safe_to_delete=False,
                        summary="Large file",
                        recommendation="Review whether this file is still needed.",
                        modified_ts=node.modified_ts,
                    )
                )

            if is_dir and node.size_bytes >= large_dir_bytes:
                _record(
                    Insight(
                        path=path,
                        size_bytes=node.size_bytes,
                        category=InsightCategory.LARGE_DIRECTORY,
                        safe_to_delete=False,
                        summary="Large directory",
                        recommendation="Inspect directory contents for cleanup opportunities.",
                        modified_ts=node.modified_ts,
                    )
                )

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

    all_insights.sort(key=lambda x: x.size_bytes, reverse=True)

    return InsightBundle(
        insights=all_insights,
        category_counts=category_counts,
        category_sizes=category_sizes,
        category_paths=category_paths,
    )


def _insight_from_rule(node: ScanNode, rule: PatternRule) -> Insight:
    return Insight(
        path=node.path,
        size_bytes=node.size_bytes,
        category=rule.category,
        safe_to_delete=rule.safe_to_delete,
        summary=rule.name,
        recommendation=rule.recommendation,
        modified_ts=node.modified_ts,
    )


def filter_insights(
    bundle: InsightBundle, categories: set[InsightCategory]
) -> list[Insight]:
    return [item for item in bundle.insights if item.category in categories]
