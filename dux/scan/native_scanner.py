from __future__ import annotations

from typing import override

from dux.models.enums import NodeKind
from dux.models.scan import ScanNode
from dux.scan._base import ThreadedScannerBase
from dux.services.tree import LEAF_CHILDREN


class NativeScanner(ThreadedScannerBase):
    def __init__(self, workers: int = 8) -> None:
        super().__init__(workers=workers)

    @override
    def _scan_dir(self, parent: ScanNode, path: str) -> tuple[list[ScanNode], int, int, int]:
        from dux._walker import scan_dir_nodes  # type: ignore[import-not-found]

        return scan_dir_nodes(path, parent, LEAF_CHILDREN, NodeKind.DIRECTORY, NodeKind.FILE, ScanNode)  # type: ignore[no-any-return]
