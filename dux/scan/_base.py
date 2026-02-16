from __future__ import annotations

import queue
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

from result import Err, Ok

from dux.models.enums import NodeKind
from dux.models.scan import (
    CancelCheck,
    ProgressCallback,
    ScanError,
    ScanErrorCode,
    ScanNode,
    ScanOptions,
    ScanResult,
    ScanSnapshot,
    ScanStats,
)
from dux.services.fs import DEFAULT_FS, FileSystem
from dux.services.tree import finalize_sizes


@dataclass(slots=True, frozen=True)
class _Task:
    node: ScanNode
    depth: int


def resolve_root(path: str, fs: FileSystem) -> str | ScanError:
    """Validate and resolve a scan root path.

    Returns the resolved absolute path, or a ``ScanError`` on failure.
    """
    expanded = fs.expanduser(path)
    if not fs.exists(expanded):
        return ScanError(
            code=ScanErrorCode.NOT_FOUND,
            path=expanded,
            message="Path does not exist",
        )

    resolved = fs.absolute(expanded)
    try:
        root_stat = fs.stat(resolved)
    except OSError as exc:
        return ScanError(
            code=ScanErrorCode.ROOT_STAT_FAILED,
            path=resolved,
            message=f"Cannot stat root: {exc}",
        )
    if not root_stat.is_dir:
        return ScanError(
            code=ScanErrorCode.NOT_DIRECTORY,
            path=resolved,
            message="Path is not a directory",
        )
    return resolved


class ThreadedScannerBase(ABC):
    def __init__(self, workers: int = 8, fs: FileSystem = DEFAULT_FS) -> None:
        self._workers = max(1, workers)
        self._fs = fs

    @abstractmethod
    def _scan_dir(self, parent: ScanNode, path: str) -> tuple[list[ScanNode], int, int, int]:
        """Read a directory, create nodes, and append them to *parent*.children.

        Returns ``(dir_child_nodes, file_count, dir_count, error_count)``.
        """

    def scan(
        self,
        path: str,
        options: ScanOptions,
        progress_callback: ProgressCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> ScanResult:
        resolved = resolve_root(path, self._fs)
        if isinstance(resolved, ScanError):
            return Err(resolved)
        resolved_root = resolved

        root_name = resolved_root.rsplit("/", 1)[-1] or resolved_root
        root_node = ScanNode(
            path=resolved_root,
            name=root_name,
            kind=NodeKind.DIRECTORY,
            size_bytes=0,
            disk_usage=0,
            children=[],
        )

        q: queue.Queue[_Task | None] = queue.Queue()
        q.put(_Task(root_node, 0))

        stats = ScanStats(files=0, directories=1, access_errors=0)
        stats_lock = threading.Lock()
        cancelled = threading.Event()

        def _is_cancelled() -> bool:
            if cancelled.is_set():
                return True
            if cancel_check is not None and cancel_check():
                cancelled.set()
                return True
            return False

        def emit_progress(current_path: str, local_files: int, local_dirs: int) -> None:
            if progress_callback is None:
                return
            with stats_lock:
                f = stats.files + local_files
                d = stats.directories + local_dirs
            progress_callback(current_path, f, d)

        def run_worker() -> None:
            local_files = 0
            local_dirs = 0
            local_errors = 0

            def _flush_local() -> None:
                nonlocal local_files, local_dirs, local_errors
                if local_files or local_dirs or local_errors:
                    with stats_lock:
                        stats.files += local_files
                        stats.directories += local_dirs
                        stats.access_errors += local_errors
                    local_files = local_dirs = local_errors = 0

            while True:
                task = q.get()
                if task is None:
                    _flush_local()
                    q.task_done()
                    break

                if _is_cancelled():
                    q.task_done()
                    continue

                try:
                    dir_children, files, dirs, errs = self._scan_dir(task.node, task.node.path)
                    prev_total = local_files + local_dirs
                    local_files += files
                    local_dirs += dirs
                    local_errors += errs

                    within_depth = options.max_depth is None or task.depth < options.max_depth
                    if within_depth:
                        for dir_node in dir_children:
                            q.put(_Task(dir_node, task.depth + 1))

                    new_total = local_files + local_dirs
                    if new_total // 100 > prev_total // 100:
                        emit_progress(task.node.path, local_files, local_dirs)
                except Exception:  # noqa: BLE001
                    local_errors += 1
                finally:
                    _flush_local()
                    q.task_done()

        num_workers = self._workers
        threads = [threading.Thread(target=run_worker, daemon=True) for _ in range(num_workers)]
        for thread in threads:
            thread.start()
        q.join()
        for _ in threads:
            q.put(None)
        q.join()
        for thread in threads:
            thread.join(timeout=0.3)

        if cancelled.is_set():
            return Err(
                ScanError(
                    code=ScanErrorCode.CANCELLED,
                    path=resolved_root,
                    message="Scan cancelled",
                )
            )

        finalize_sizes(root_node)
        return Ok(ScanSnapshot(root=root_node, stats=stats))
