from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path

from result import Err, Ok

from dux.services.fs import DEFAULT_FS, FileSystem
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


@dataclass(slots=True, frozen=True)
class _Task:
    node: ScanNode
    depth: int


def _finalize_sizes(root: ScanNode) -> None:
    stack: list[ScanNode] = []
    visit: list[ScanNode] = [root]
    while visit:
        node = visit.pop()
        if not node.is_dir:
            continue
        stack.append(node)
        visit.extend(node.children)
    for node in reversed(stack):
        node.size_bytes = sum(child.size_bytes for child in node.children)
        node.disk_usage = sum(child.disk_usage for child in node.children)
        node.children.sort(key=lambda x: x.disk_usage, reverse=True)


def scan_path(
    path: str | Path,
    options: ScanOptions,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
    workers: int = 8,
    fs: FileSystem = DEFAULT_FS,
) -> ScanResult:
    expanded = fs.expanduser(str(path))
    if not fs.exists(expanded):
        return Err(
            ScanError(
                code=ScanErrorCode.NOT_FOUND,
                path=expanded,
                message="Path does not exist",
            )
        )

    resolved_root = fs.absolute(expanded)
    try:
        root_stat = fs.stat(resolved_root)
    except OSError as exc:
        return Err(
            ScanError(
                code=ScanErrorCode.ROOT_STAT_FAILED,
                path=resolved_root,
                message=f"Cannot stat root: {exc}",
            )
        )
    if not root_stat.is_dir:
        return Err(
            ScanError(
                code=ScanErrorCode.NOT_DIRECTORY,
                path=resolved_root,
                message="Path is not a directory",
            )
        )

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
                for entry in fs.scandir(task.node.path):
                    if _is_cancelled():
                        break

                    st = entry.stat
                    if st is None:
                        local_errors += 1
                        continue

                    node = ScanNode(
                        path=entry.path,
                        name=entry.name,
                        kind=NodeKind.DIRECTORY if st.is_dir else NodeKind.FILE,
                        size_bytes=0 if st.is_dir else st.size,
                        disk_usage=0 if st.is_dir else st.disk_usage,
                        children=[],
                    )
                    task.node.children.append(node)

                    if st.is_dir:
                        local_dirs += 1
                        within_depth = options.max_depth is None or task.depth < options.max_depth
                        if within_depth:
                            q.put(_Task(node, task.depth + 1))
                    else:
                        local_files += 1

                    if (local_dirs + local_files) % 100 == 0:
                        emit_progress(node.path, local_files, local_dirs)
            except OSError:
                local_errors += 1
            finally:
                _flush_local()
                q.task_done()

    num_workers = max(1, workers)
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

    _finalize_sizes(root_node)
    return Ok(ScanSnapshot(root=root_node, stats=stats))
