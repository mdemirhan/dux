from __future__ import annotations

import os
import sys
import tempfile

import pytest
from result import Ok

from dux.models.scan import ScanOptions
from dux.scan.posix_scanner import PosixScanner


def test_posix_scanner_basic() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "sub"))
        with open(os.path.join(tmpdir, "a.txt"), "wb") as f:
            f.write(b"x" * 100)
        with open(os.path.join(tmpdir, "sub", "b.txt"), "wb") as f:
            f.write(b"y" * 200)

        scanner = PosixScanner(workers=4)
        result = scanner.scan(tmpdir, ScanOptions())

        assert isinstance(result, Ok)
        snapshot = result.unwrap()
        assert snapshot.stats.files == 2
        assert snapshot.stats.directories >= 2
        assert snapshot.root.size_bytes == 300
        assert snapshot.root.path == tmpdir


def test_posix_scanner_max_depth() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "lvl1", "lvl2"))
        with open(os.path.join(tmpdir, "lvl1", "lvl2", "deep.txt"), "wb") as f:
            f.write(b"z" * 50)

        scanner = PosixScanner(workers=4)
        result = scanner.scan(tmpdir, ScanOptions(max_depth=0))

        assert isinstance(result, Ok)
        snapshot = result.unwrap()
        lvl1 = next(c for c in snapshot.root.children if c.name == "lvl1")
        assert lvl1.children == []


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_macos_scanner_basic() -> None:
    from dux.scan.macos_scanner import MacOSScanner

    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "sub"))
        with open(os.path.join(tmpdir, "a.txt"), "wb") as f:
            f.write(b"x" * 100)
        with open(os.path.join(tmpdir, "sub", "b.txt"), "wb") as f:
            f.write(b"y" * 200)

        scanner = MacOSScanner(workers=4)
        result = scanner.scan(tmpdir, ScanOptions())

        assert isinstance(result, Ok)
        snapshot = result.unwrap()
        assert snapshot.stats.files == 2
        assert snapshot.stats.directories >= 2
        assert snapshot.root.size_bytes == 300
        assert snapshot.root.path == tmpdir


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_macos_scanner_max_depth() -> None:
    from dux.scan.macos_scanner import MacOSScanner

    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "lvl1", "lvl2"))
        with open(os.path.join(tmpdir, "lvl1", "lvl2", "deep.txt"), "wb") as f:
            f.write(b"z" * 50)

        scanner = MacOSScanner(workers=4)
        result = scanner.scan(tmpdir, ScanOptions(max_depth=0))

        assert isinstance(result, Ok)
        snapshot = result.unwrap()
        lvl1 = next(c for c in snapshot.root.children if c.name == "lvl1")
        assert lvl1.children == []
