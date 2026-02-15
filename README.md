# DiskAnalysis

Production-quality Python terminal disk analyzer with CLI and interactive TUI.

## Requirements

- Python 3.13+
- `uv`
- macOS or Linux (Windows is not implemented yet)

## Setup

```bash
uv sync --extra dev
```

## Run

```bash
uv run diskanalysis [PATH]
uv run diskanalysis --summary [PATH]
uv run diskanalysis --temp [PATH]
uv run diskanalysis --cache [PATH]
uv run diskanalysis --temp --summary [PATH]
uv run diskanalysis --cache --summary [PATH]
uv run diskanalysis --sample-config
```

If no `PATH` is provided, the current directory is analyzed.

## Config

- Path: `~/.config/diskanalysis/config.json`
- Missing config: defaults are used silently
- Invalid config: warning is printed and defaults are used

Generate full sample config:

```bash
uv run diskanalysis --sample-config
```

Config is fully rule-driven:

- temp patterns
- cache patterns
- build artifact patterns
- custom patterns
- thresholds
- additional temp/cache paths
- depth controls

## TUI Views

- `Overview`
- `Browse`
- `Large Dir`
- `Large File`
- `Temp`

## Keybindings

Global:

- `q` / `Ctrl+C`: quit
- `?`: help
- `Tab` / `Shift+Tab`: next/previous view
- `o`, `b`, `t`, `d`, `f`: jump to view

Browse:

- `j/k` or arrows: move
- `h/l` or left/right: collapse/expand, parent/drill-in
- `Enter`: drill in
- `Backspace`: drill out
- `Space`: toggle expand/collapse
- `gg`/`Home`: top
- `G`/`End`: bottom
- `PgUp`/`PgDn`, `Ctrl+U`/`Ctrl+D`: page

Temp:

- `[` / `]`: prev/next page

## Test

```bash
uv run pytest
```

## Safety

DiskAnalysis is analysis-only. It does not delete, move, or modify scanned files.
