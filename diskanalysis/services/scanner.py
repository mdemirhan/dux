from __future__ import annotations

import os
import queue
import stat as statmod
import threading
from dataclasses import dataclass
from pathlib import Path

from result import Err, Ok

from diskanalysis.models.enums import NodeKind
from diskanalysis.models.scan import (
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
        total = sum(child.size_bytes for child in node.children)
        node.children.sort(key=lambda x: x.size_bytes, reverse=True)
        node.size_bytes = total


def scan_path(
    path: str | Path,
    options: ScanOptions,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
    workers: int = 8,
) -> ScanResult:
    root_path = Path(path).expanduser()
    if not root_path.exists():
        return Err(
            ScanError(
                code=ScanErrorCode.NOT_FOUND,
                path=str(root_path),
                message="Path does not exist",
            )
        )

    resolved_root = str(root_path.absolute())
    follow_symlinks = options.follow_symlinks
    try:
        root_stat = root_path.stat(follow_symlinks=follow_symlinks)
    except OSError as exc:
        return Err(
            ScanError(
                code=ScanErrorCode.ROOT_STAT_FAILED,
                path=resolved_root,
                message=f"Cannot stat root: {exc}",
            )
        )
    if not statmod.S_ISDIR(root_stat.st_mode):
        return Err(
            ScanError(
                code=ScanErrorCode.NOT_DIRECTORY,
                path=resolved_root,
                message="Path is not a directory",
            )
        )

    root_node = ScanNode(
        path=resolved_root,
        name=root_path.name or resolved_root,
        kind=NodeKind.DIRECTORY,
        size_bytes=0,
        modified_ts=root_stat.st_mtime,
        children=[],
    )

    q: queue.Queue[_Task | None] = queue.Queue()
    q.put(_Task(root_node, 0))

    stats = ScanStats(files=0, directories=1, bytes_total=0, access_errors=0)
    stats_lock = threading.Lock()
    cancelled = threading.Event()

    def emit_progress(current_path: str) -> None:
        if progress_callback is None:
            return
        with stats_lock:
            f, d = stats.files, stats.directories
        progress_callback(current_path, f, d)

    def run_worker() -> None:
        while True:
            task = q.get()
            if task is None:
                q.task_done()
                break

            if cancelled.is_set():
                q.task_done()
                continue

            if cancel_check is not None and cancel_check():
                cancelled.set()
                q.task_done()
                continue

            try:
                with os.scandir(task.node.path) as entries:
                    for entry in entries:
                        if cancelled.is_set():
                            break
                        if cancel_check is not None and cancel_check():
                            cancelled.set()
                            break

                        try:
                            stat_result = entry.stat(follow_symlinks=follow_symlinks)
                        except OSError:
                            with stats_lock:
                                stats.access_errors += 1
                            continue

                        is_dir = statmod.S_ISDIR(stat_result.st_mode)
                        node = ScanNode(
                            path=entry.path,
                            name=entry.name,
                            kind=NodeKind.DIRECTORY if is_dir else NodeKind.FILE,
                            size_bytes=0 if is_dir else stat_result.st_size,
                            modified_ts=stat_result.st_mtime,
                            children=[],
                        )
                        task.node.children.append(node)

                        if is_dir:
                            with stats_lock:
                                stats.directories += 1
                            within_depth = (
                                options.max_depth is None
                                or task.depth < options.max_depth
                            )
                            if within_depth:
                                q.put(_Task(node, task.depth + 1))
                        else:
                            with stats_lock:
                                stats.files += 1
                                stats.bytes_total += node.size_bytes
                        emit_progress(node.path)
            except OSError:
                with stats_lock:
                    stats.access_errors += 1
            finally:
                q.task_done()

    num_workers = max(1, workers)
    threads = [
        threading.Thread(target=run_worker, daemon=True) for _ in range(num_workers)
    ]
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
    stats.bytes_total = root_node.size_bytes
    return Ok(ScanSnapshot(root=root_node, stats=stats))
