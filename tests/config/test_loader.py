from __future__ import annotations

from result import Err, Ok

from diskanalysis.config.loader import load_config
from tests.fs_mock import MemoryFileSystem


def test_load_config_missing_uses_defaults() -> None:
    fs = MemoryFileSystem()
    result = load_config(path="/missing.json", fs=fs)
    assert isinstance(result, Ok)
    cfg = result.unwrap()
    assert cfg.temp_patterns


def test_load_config_invalid_returns_warning() -> None:
    fs = MemoryFileSystem().add_file("/config.json", content="not-json")
    result = load_config(path="/config.json", fs=fs)
    assert isinstance(result, Err)
    warning = result.unwrap_err()
    assert "failed reading config" in warning.lower()
