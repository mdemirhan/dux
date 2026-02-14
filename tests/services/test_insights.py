from __future__ import annotations

import time

from diskanalysis.config.defaults import default_config
from diskanalysis.config.schema import PatternRule
from diskanalysis.models.enums import InsightCategory, NodeKind
from diskanalysis.models.scan import ScanNode
from diskanalysis.services.insights import generate_insights


def _tree_with(*children: ScanNode) -> ScanNode:
    total = sum(child.size_bytes for child in children)
    return ScanNode(
        path="/root",
        name="root",
        kind=NodeKind.DIRECTORY,
        size_bytes=total,
        modified_ts=time.time(),
        children=list(children),
    )


def _file(path: str, size: int, modified: float | None = None) -> ScanNode:
    return ScanNode(
        path=path,
        name=path.rsplit("/", 1)[-1],
        kind=NodeKind.FILE,
        size_bytes=size,
        modified_ts=modified or time.time(),
        children=[],
    )


def _dir(path: str, size: int, *children: ScanNode) -> ScanNode:
    return ScanNode(
        path=path,
        name=path.rsplit("/", 1)[-1],
        kind=NodeKind.DIRECTORY,
        size_bytes=size,
        modified_ts=time.time(),
        children=list(children),
    )


def test_temp_analyzer_path_matching_and_threshold_logic() -> None:
    config = default_config()
    node = _file("/root/tmp/trace.log", size=2 * 1024 * 1024)
    bundle = generate_insights(_tree_with(node), config)

    assert any(item.category is InsightCategory.TEMP for item in bundle.insights)


def test_cache_analyzer_path_matching_and_threshold_logic() -> None:
    config = default_config()
    node = _dir("/root/.cache/pip", size=3 * 1024 * 1024)
    bundle = generate_insights(_tree_with(node), config)

    assert any(item.category is InsightCategory.CACHE for item in bundle.insights)


def test_build_artifact_detection() -> None:
    config = default_config()
    node = _dir(
        "/root/project/node_modules",
        2 * 1024 * 1024,
        _file("/root/project/node_modules/a.js", 100),
    )
    bundle = generate_insights(_tree_with(node), config)

    assert any(
        item.category is InsightCategory.BUILD_ARTIFACT for item in bundle.insights
    )


def test_dedup_by_path() -> None:
    config = default_config()
    node = _dir("/root/__pycache__", size=100)
    bundle = generate_insights(_tree_with(node), config)

    matched = [item for item in bundle.insights if item.path == "/root/__pycache__"]
    assert len(matched) == 1


def test_temp_and_cache_insights_generated() -> None:
    config = default_config()
    temp = _dir("/root/tmp/cache", size=100)
    cache = _dir("/root/.cache/pip", size=200)
    bundle = generate_insights(_tree_with(temp, cache), config)

    categories = {item.category for item in bundle.insights}
    assert InsightCategory.TEMP in categories
    assert InsightCategory.CACHE in categories


def test_custom_pattern_detection() -> None:
    config = default_config()
    config.custom_patterns = [
        PatternRule(
            name="ISO",
            pattern="**/*.iso",
            category=InsightCategory.CUSTOM,
            safe_to_delete=False,
            recommendation="Review archives",
            apply_to="file",
            stop_recursion=False,
        )
    ]
    node = _file("/root/images/archive.iso", size=1024)
    bundle = generate_insights(_tree_with(node), config)

    assert any(item.category is InsightCategory.CUSTOM for item in bundle.insights)


def test_case_insensitive_matching() -> None:
    """Pattern matching should be case-insensitive (e.g. .DS_STORE matches .DS_Store rule)."""
    config = default_config()
    # Mixed-case path that should match the lowercase ".ds_store" pattern
    node = _file("/root/project/.DS_STORE", size=4096)
    bundle = generate_insights(_tree_with(node), config)

    assert any(item.category is InsightCategory.TEMP for item in bundle.insights)


def test_case_insensitive_extension_matching() -> None:
    """File extensions like .LOG should match *.log rules."""
    config = default_config()
    node = _file("/root/app/debug.LOG", size=1024)
    bundle = generate_insights(_tree_with(node), config)

    assert any(item.category is InsightCategory.TEMP for item in bundle.insights)


def test_case_insensitive_directory_matching() -> None:
    """Directory names like Node_Modules should match node_modules rule."""
    config = default_config()
    node = _dir(
        "/root/project/Node_Modules",
        2 * 1024 * 1024,
        _file("/root/project/Node_Modules/a.js", 100),
    )
    bundle = generate_insights(_tree_with(node), config)

    assert any(
        item.category is InsightCategory.BUILD_ARTIFACT for item in bundle.insights
    )
