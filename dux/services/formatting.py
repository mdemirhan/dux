from __future__ import annotations

UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


def format_bytes(size: int) -> str:
    if size <= 0:
        return "0 B"
    value = float(size)
    unit = 0
    while value >= 1024 and unit < len(UNITS) - 1:
        value /= 1024.0
        unit += 1
    if unit == 0:
        return f"{int(value)} {UNITS[unit]}"
    return f"{value:.1f} {UNITS[unit]}"


def relative_bar(size: int, total: int, width: int = 16) -> str:
    if width <= 0 or total <= 0:
        return ""
    ratio = min(1.0, max(0.0, size / total))
    filled = int(round(ratio * width))
    return "█" * filled + "░" * max(0, width - filled)
