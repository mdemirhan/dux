from __future__ import annotations

from result import Err, Ok

from dux.models.scan import ScanErrorCode, ScanOptions
from dux.scan import PythonScanner
from tests.fs_mock import MemoryFileSystem


def test_scanner_returns_valid_results() -> None:
    fs = (
        MemoryFileSystem()
        .add_dir("/root")
        .add_file("/root/big.bin", size=128)
        .add_file("/root/small.bin", size=32)
        .add_dir("/root/sub")
        .add_file("/root/sub/nested.bin", size=64)
    )

    result = PythonScanner(workers=1, fs=fs).scan("/root", ScanOptions())

    assert isinstance(result, Ok)
    snapshot = result.unwrap()
    assert snapshot.stats.files == 3
    assert snapshot.stats.directories >= 2
    assert snapshot.root.size_bytes == 224


def test_missing_path_returns_error() -> None:
    fs = MemoryFileSystem()

    result = PythonScanner(workers=1, fs=fs).scan("/does-not-exist", ScanOptions())

    assert isinstance(result, Err)
    error = result.unwrap_err()
    assert error.code is ScanErrorCode.NOT_FOUND
    assert "does not exist" in error.message.lower()


def test_children_sorted_by_size_descending() -> None:
    fs = (
        MemoryFileSystem()
        .add_dir("/root")
        .add_file("/root/a.bin", size=10)
        .add_file("/root/b.bin", size=100)
        .add_file("/root/c.bin", size=50)
    )

    result = PythonScanner(workers=1, fs=fs).scan("/root", ScanOptions())
    assert isinstance(result, Ok)
    snapshot = result.unwrap()

    names = [child.name for child in snapshot.root.children if not child.is_dir]
    assert names == ["b.bin", "c.bin", "a.bin"]


def test_max_depth_respected() -> None:
    fs = (
        MemoryFileSystem()
        .add_dir("/root")
        .add_dir("/root/lvl1")
        .add_dir("/root/lvl1/lvl2")
        .add_file("/root/lvl1/lvl2/f.bin", size=20)
    )

    result = PythonScanner(workers=1, fs=fs).scan("/root", ScanOptions(max_depth=0))
    assert isinstance(result, Ok)
    snapshot = result.unwrap()

    lvl1 = next(child for child in snapshot.root.children if child.name == "lvl1")
    assert lvl1.children == []


def test_access_error_counted() -> None:
    fs = MemoryFileSystem().add_dir("/root").add_file("/root/ok.bin", size=10)

    from dux.services.fs import DirEntry

    original_scandir = fs.scandir

    def patched_scandir(path: str) -> list[DirEntry]:
        entries = original_scandir(path)
        if path == "/root":
            entries.append(DirEntry(path="/root/broken", name="broken", stat=None))
        return entries

    fs.scandir = patched_scandir  # type: ignore[assignment]

    result = PythonScanner(workers=1, fs=fs).scan("/root", ScanOptions())
    assert isinstance(result, Ok)
    snapshot = result.unwrap()
    assert snapshot.stats.access_errors == 1
    assert snapshot.stats.files == 1


def test_progress_callback_invoked() -> None:
    fs = MemoryFileSystem().add_dir("/root")
    for idx in range(150):
        fs.add_file(f"/root/f{idx}.bin", size=1)

    calls: list[tuple[str, int, int]] = []

    def on_progress(path: str, files: int, dirs: int) -> None:
        calls.append((path, files, dirs))

    result = PythonScanner(workers=1, fs=fs).scan("/root", ScanOptions(), progress_callback=on_progress)
    assert isinstance(result, Ok)
    assert len(calls) >= 1
    for _, files, _ in calls:
        assert files > 0


def test_cancellation_respected() -> None:
    # Cancellation is checked between directories, so we need multiple dirs
    fs = MemoryFileSystem().add_dir("/root")
    for idx in range(5):
        fs.add_dir(f"/root/d{idx}")
        for jdx in range(10):
            fs.add_file(f"/root/d{idx}/f{jdx}.bin", size=1)

    calls = 0

    def cancel() -> bool:
        nonlocal calls
        calls += 1
        return calls > 2

    result = PythonScanner(workers=1, fs=fs).scan("/root", ScanOptions(), cancel_check=cancel)
    assert isinstance(result, Err)
    error = result.unwrap_err()
    assert error.code is ScanErrorCode.CANCELLED
    assert "cancel" in error.message.lower()
