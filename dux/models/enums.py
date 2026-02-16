from __future__ import annotations

from enum import Enum, IntFlag


class NodeKind(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"


class InsightCategory(str, Enum):
    TEMP = "temp"
    CACHE = "cache"
    BUILD_ARTIFACT = "build_artifact"


class ApplyTo(IntFlag):
    FILE = 1
    DIR = 2
    BOTH = FILE | DIR
