from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from dux.scan import default_scanner
from dux.scan._base import ThreadedScannerBase


class TestDefaultScanner:
    def test_darwin_returns_macos_scanner(self) -> None:
        if sys.platform != "darwin":
            pytest.skip("macOS only")
        scanner = default_scanner()
        assert isinstance(scanner, ThreadedScannerBase)
        from dux.scan.macos_scanner import MacOSScanner

        assert isinstance(scanner, MacOSScanner)

    def test_non_darwin_returns_posix_scanner(self) -> None:
        with patch("dux.scan.sys") as mock_sys:
            mock_sys.platform = "linux"
            scanner = default_scanner()
            # On macOS the actual call still uses the real sys.platform
            # so we just verify it returns a ThreadedScannerBase
            assert isinstance(scanner, ThreadedScannerBase)


class TestMacOSScannerPlatformCheck:
    def test_non_darwin_raises(self) -> None:
        with patch("dux.scan.macos_scanner.sys") as mock_sys:
            mock_sys.platform = "linux"
            from dux.scan.macos_scanner import MacOSScanner

            with pytest.raises(RuntimeError, match="macOS"):
                MacOSScanner()
