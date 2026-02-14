from __future__ import annotations

from enum import Enum


class NodeKind(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"


class InsightCategory(str, Enum):
    TEMP = "temp"
    CACHE = "cache"
    BUILD_ARTIFACT = "build_artifact"
    CUSTOM = "custom"
