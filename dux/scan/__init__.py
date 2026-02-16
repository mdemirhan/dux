from __future__ import annotations

import sys
from typing import Protocol

from dux.models.scan import CancelCheck, ProgressCallback, ScanOptions, ScanResult
from dux.scan._base import ThreadedScannerBase, resolve_root
from dux.scan.python_scanner import PythonScanner


class Scanner(Protocol):
    def scan(
        self,
        path: str,
        options: ScanOptions,
        progress_callback: ProgressCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> ScanResult: ...


def default_scanner(workers: int = 8) -> ThreadedScannerBase:
    """Return the best available scanner for the current platform."""
    if sys.platform == "darwin":
        from dux.scan.macos_scanner import MacOSScanner

        return MacOSScanner(workers=workers)

    from dux.scan.posix_scanner import PosixScanner

    return PosixScanner(workers=workers)


__all__ = [
    "PythonScanner",
    "Scanner",
    "ThreadedScannerBase",
    "default_scanner",
    "resolve_root",
]
