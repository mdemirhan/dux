from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from diskanalysis.services.fs import DirEntry, StatResult


@dataclass
class _MockEntry:
    is_dir: bool
    size: int
    mtime: float
    content: str


class MemoryFileSystem:
    def __init__(self) -> None:
        self._entries: dict[str, _MockEntry] = {}

    def add_dir(self, path: str, mtime: float = 0.0) -> MemoryFileSystem:
        self._entries[self._normalize(path)] = _MockEntry(
            is_dir=True, size=0, mtime=mtime, content=""
        )
        return self

    def add_file(
        self,
        path: str,
        size: int = 0,
        mtime: float = 0.0,
        content: str = "",
    ) -> MemoryFileSystem:
        key = self._normalize(path)
        # auto-create parent dirs
        for parent in reversed(PurePosixPath(key).parents):
            pk = str(parent)
            if pk not in self._entries:
                self._entries[pk] = _MockEntry(
                    is_dir=True, size=0, mtime=0.0, content=""
                )
        self._entries[key] = _MockEntry(
            is_dir=False, size=size, mtime=mtime, content=content
        )
        return self

    def expanduser(self, path: str) -> str:
        return path.replace("~", "/mock/home")

    def exists(self, path: str) -> bool:
        return self._normalize(path) in self._entries

    def absolute(self, path: str) -> str:
        return self._normalize(path)

    def stat(self, path: str) -> StatResult:
        key = self._normalize(path)
        entry = self._entries.get(key)
        if entry is None:
            raise OSError(f"No such file or directory: '{key}'")
        return StatResult(size=entry.size, mtime=entry.mtime, is_dir=entry.is_dir)

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        key = self._normalize(path)
        entry = self._entries.get(key)
        if entry is None:
            raise OSError(f"No such file or directory: '{key}'")
        return entry.content

    def scandir(self, path: str) -> list[DirEntry]:
        key = self._normalize(path)
        entry = self._entries.get(key)
        if entry is None:
            raise OSError(f"No such file or directory: '{key}'")
        prefix = key.rstrip("/") + "/"
        result: list[DirEntry] = []
        seen: set[str] = set()
        for p, mock in self._entries.items():
            if not p.startswith(prefix):
                continue
            remainder = p[len(prefix) :]
            if "/" in remainder:
                child_name = remainder.split("/", 1)[0]
                child_path = prefix + child_name
            else:
                child_name = remainder
                child_path = prefix + remainder
            if child_path not in seen:
                seen.add(child_path)
                child_entry = self._entries.get(child_path)
                st = (
                    StatResult(
                        size=child_entry.size,
                        mtime=child_entry.mtime,
                        is_dir=child_entry.is_dir,
                    )
                    if child_entry is not None
                    else None
                )
                result.append(DirEntry(path=child_path, name=child_name, stat=st))
        return result

    @staticmethod
    def _normalize(path: str) -> str:
        return path.rstrip("/")
