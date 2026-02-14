from __future__ import annotations

import heapq
from collections.abc import Iterator

from diskanalysis.models.enums import NodeKind
from diskanalysis.models.scan import ScanNode


def iter_nodes(root: ScanNode) -> Iterator[ScanNode]:
    """Iterate all nodes in the tree rooted at *root* (depth-first)."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.children)


def top_nodes(root: ScanNode, n: int, kind: NodeKind | None = None) -> list[ScanNode]:
    """Return the *n* largest nodes, excluding *root*.

    When *kind* is given, only nodes of that kind are considered.
    """
    items = (
        node
        for node in iter_nodes(root)
        if node.path != root.path and (kind is None or node.kind is kind)
    )
    return heapq.nlargest(n, items, key=lambda node: node.size_bytes)
