from __future__ import annotations

import os
import stat as statmod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol


@dataclass(slots=True, frozen=True)
class StatResult:
    size: int
    mtime: float
    is_dir: bool


@dataclass(slots=True, frozen=True)
class DirEntry:
    path: str
    name: str
    stat: StatResult | None = None


class FileSystem(Protocol):
    def expanduser(self, path: str) -> str: ...

    def exists(self, path: str) -> bool: ...

    def absolute(self, path: str) -> str: ...

    def stat(self, path: str) -> StatResult: ...

    def scandir(self, path: str) -> Iterable[DirEntry]: ...

    def read_text(self, path: str, encoding: str = "utf-8") -> str: ...


class OsFileSystem:
    def expanduser(self, path: str) -> str:
        return str(Path(path).expanduser())

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def absolute(self, path: str) -> str:
        return str(Path(path).absolute())

    def stat(self, path: str) -> StatResult:
        st = os.stat(path, follow_symlinks=False)
        return StatResult(
            size=st.st_size,
            mtime=st.st_mtime,
            is_dir=statmod.S_ISDIR(st.st_mode),
        )

    def scandir(self, path: str) -> Iterable[DirEntry]:
        with os.scandir(path) as entries:
            for e in entries:
                try:
                    st = e.stat(follow_symlinks=False)
                    sr = StatResult(
                        size=st.st_size,
                        mtime=st.st_mtime,
                        is_dir=statmod.S_ISDIR(st.st_mode),
                    )
                except OSError:
                    sr = None
                yield DirEntry(path=e.path, name=e.name, stat=sr)

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        return Path(path).read_text(encoding=encoding)


DEFAULT_FS: FileSystem = OsFileSystem()
