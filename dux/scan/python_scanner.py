from __future__ import annotations

from typing import override

from dux.models.enums import NodeKind
from dux.models.scan import ScanNode
from dux.scan._base import ThreadedScannerBase
from dux.services.fs import DEFAULT_FS, FileSystem
from dux.services.tree import LEAF_CHILDREN


class PythonScanner(ThreadedScannerBase):
    def __init__(self, workers: int = 8, fs: FileSystem = DEFAULT_FS) -> None:
        super().__init__(workers=workers, fs=fs)

    @override
    def _scan_dir(self, parent: ScanNode, path: str) -> tuple[list[ScanNode], int, int, int]:
        dir_children: list[ScanNode] = []
        errors = 0
        files = 0
        dirs = 0
        for entry in self._fs.scandir(path):
            st = entry.stat
            if st is None:
                errors += 1
                continue
            if st.is_dir:
                node = ScanNode(
                    path=entry.path,
                    name=entry.name,
                    kind=NodeKind.DIRECTORY,
                    size_bytes=0,
                    disk_usage=0,
                    children=[],
                )
                parent.children.append(node)
                dir_children.append(node)
                dirs += 1
            else:
                node = ScanNode(
                    path=entry.path,
                    name=entry.name,
                    kind=NodeKind.FILE,
                    size_bytes=st.size,
                    disk_usage=st.disk_usage,
                    children=LEAF_CHILDREN,
                )
                parent.children.append(node)
                files += 1
        return dir_children, files, dirs, errors
