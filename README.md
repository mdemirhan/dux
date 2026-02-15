# dux

A fast, interactive terminal disk usage analyzer for macOS and Linux. Scans directories in parallel, categorizes files (temp, cache, build artifacts), and presents results in a rich TUI with vim-style navigation.

> **100% AI-written.** The vast majority of this codebase was written by Claude (Anthropic), with contributions from Codex (OpenAI). Human involvement was limited to directing, reviewing, and benchmarking.

## Features

- **Parallel scanning** with configurable thread pool (default 4 workers)
- **Interactive TUI** with 5 views, vim keybindings, search/filter, pagination
- **Composable CLI flags** — `--top-temp`, `--top-cache`, `--top-dirs`, `--top-files` each print their own table and can be freely combined
- **670+ built-in patterns** for detecting temp files, caches, and build artifacts across dozens of ecosystems (Node, Python, Rust, Go, JVM, Swift, C++, and more)
- **Fully configurable** via JSON config with pattern overrides and custom paths
- **Analysis-only** — never deletes, moves, or modifies your files

## Quick Start

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
# Clone and install
git clone https://github.com/mdemirhan/dux.git
cd dux
uv sync

# Analyze current directory (summary table)
uv run dux

# Analyze a specific path
uv run dux ~/src

# Include logical file size column
uv run dux -s ~/src

# Largest temp/build artifacts
uv run dux -t ~/src

# Combine flags: summary + cache + temp
uv run dux -c -t ~/src

# Interactive TUI
uv run dux -i ~/src
```

## TUI Views

Switch views with `Tab`/`Shift+Tab` or press the shortcut key directly.

| Key | View | Description |
|-----|------|-------------|
| `o` | **Overview** | Total size, file/dir counts, temp/cache/build totals, largest directories |
| `b` | **Browse** | Expandable directory tree with size bars and timestamps |
| `d` | **Directories by Size** | Paginated list of largest directories |
| `f` | **Files by Size** | Paginated list of largest individual files |
| `t` | **Temporary Files** | All detected temp, cache, and build artifact items |

## Keybindings

### Navigation

| Key | Action |
|-----|--------|
| `j` / `k` / `Arrow keys` | Move up/down |
| `gg` / `G` | Jump to top/bottom |
| `Ctrl+U` / `Ctrl+D` | Page up/down |
| `[` / `]` | Previous/next page (paginated views) |

### Browse View

| Key | Action |
|-----|--------|
| `l` / `Right` / `Enter` | Expand or drill into directory |
| `h` / `Left` / `Backspace` | Collapse or go to parent |
| `Space` | Toggle expand/collapse |

### General

| Key | Action |
|-----|--------|
| `/` | Search/filter rows |
| `Escape` | Clear active filter |
| `y` | Yank full path to clipboard |
| `Y` | Yank display name to clipboard |
| `?` | Toggle help overlay |
| `q` | Quit |

## CLI Options

```
uv run dux [PATH] [OPTIONS]
```

By default dux prints a CLI summary table. Use `--interactive` / `-i` to launch the TUI. The `--top-*` flags are composable — use multiple at once to print additional tables.

| Option | Description |
|--------|-------------|
| `--interactive` / `-i` | Launch interactive TUI |
| `--show-size` / `-s` | Show logical file size column |
| `--top-temp` / `-t` | Largest temp/build artifacts |
| `--top-cache` / `-c` | Largest cache files/directories |
| `--top-dirs` / `-d` | Largest directories |
| `--top-files` / `-f` | Largest files |
| `--top` | Number of items in `--top-*` views (default: 15) |
| `--workers` / `-w` | Number of scan threads (default: 4) |
| `--max-depth` | Maximum directory depth to scan |
| `--max-insights` | Max insights per category |
| `--overview-dirs` | Top directories shown in TUI overview |
| `--scroll-step` | Lines to jump on PgUp/PgDn in TUI |
| `--page-size` | Rows per page in TUI |
| `--sample-config` | Print full sample config and exit |

## Configuration

Config file: `~/.config/dux/config.json`

Generate a sample config with all defaults:

```bash
uv run dux --sample-config > ~/.config/dux/config.json
```

Key settings:

```json
{
  "scanWorkers": 4,
  "maxDepth": null,
  "topCount": 15,
  "pageSize": 100,
  "overviewTopDirs": 100,
  "scrollStep": 20,
  "maxInsightsPerCategory": 1000,
  "additionalTempPaths": [],
  "additionalCachePaths": [],
  "tempPatterns": [...],
  "cachePatterns": [...],
  "buildArtifactPatterns": [...]
}
```

Each pattern rule:

```json
{
  "name": "npm cache",
  "pattern": "**/.npm/**",
  "category": "cache",
  "applyTo": "both",
  "stopRecursion": false
}
```

## Performance

Benchmarked on a 2024 MacBook Pro (M4 Pro):

| Directory | Files | Dirs | Time |
|-----------|-------|------|------|
| ~/src | 295k | 38k | ~3s |
| ~ | 2.1M | 323k | ~22s |

The scanner is I/O-bound. The `FileSystem` abstraction layer adds zero measurable overhead compared to direct `os.scandir` calls (verified via wall-clock benchmarking).

## Development

```bash
# Install with dev dependencies
uv sync

# Run tests
uv run pytest

# Lint and format
uv run ruff check
uv run ruff format

# Type check
uv run basedpyright
```

## Tech Stack

| Component | Tool |
|-----------|------|
| CLI framework | [Typer](https://typer.tiangolo.com/) |
| TUI framework | [Textual](https://textual.textualize.io/) |
| Terminal rendering | [Rich](https://rich.readthedocs.io/) |
| Error handling | [result](https://github.com/rustedpy/result) (Rust-style `Result[T, E]`) |
| Type checking | [basedpyright](https://docs.basedpyright.com/) (strict mode) |
| Linting/formatting | [Ruff](https://docs.astral.sh/ruff/) |
| Testing | [pytest](https://docs.pytest.org/) |
| Package management | [uv](https://docs.astral.sh/uv/) |

## License

MIT
