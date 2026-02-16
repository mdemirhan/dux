# dux

A fast terminal disk usage analyzer for macOS and Linux. Scans directories in parallel, categorizes files (temp, cache, build artifacts), and presents results as CLI tables or an interactive TUI with vim-style navigation.

> **100% AI-written.** The vast majority of this codebase was written by Claude (Anthropic), with contributions from Codex (OpenAI). Human involvement was limited to directing, reviewing, and benchmarking.

## Screenshots

**CLI summary** (`uv run dux ~/src`)

![CLI summary table](media/summary.png)

**TUI overview** (`uv run dux -i`)

![TUI overview tab](media/overview.png)

**TUI browse** — expandable directory tree with disk usage bars

![TUI browse tab](media/browse.png)

## Features

- **Parallel scanning** with configurable thread pool (default 4 workers)
- **Interactive TUI** with 5 views, vim keybindings, search/filter, pagination
- **Composable CLI flags** — `--top-temp`, `--top-cache`, `--top-dirs`, `--top-files` each print their own table and can be freely combined
- **59 built-in pattern rules** for detecting temp files, caches, and build artifacts across dozens of ecosystems (Node, Python, Rust, Go, JVM, Swift, C++, and more)
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
| `o` | **Overview** | Total disk usage, file/dir counts, temp/cache/build totals, largest directories |
| `b` | **Browse** | Expandable directory tree with disk usage bars |
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
| `--scanner` / `-S` | Scanner variant: `auto`, `python`, `posix`, `macos` (default: auto) |
| `--verbose` / `-v` | Print GIL status, scanner, and timing info |
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

Benchmarked on a MacBook Pro M4:

| Files | Dirs | Time  |
|-------|------|-------|
| 295k | 38k | ~1.4s |
| 2.1M | 323k | ~20s  |

The scanner is I/O-bound. dux ships three scanner backends and automatically selects the best one for your platform:

| Scanner | Platform | Mechanism |
|---------|----------|-----------|
| **MacOSScanner** | macOS (default) | C extension using `getattrlistbulk` — fetches all entries + stat data in a single syscall per batch |
| **PosixScanner** | Linux (GIL enabled) | C extension using `readdir` + `lstat` — releases the GIL during I/O for better thread utilization |
| **PythonScanner** | Fallback / GIL disabled | Pure Python via `os.scandir` — also used for testing via the `FileSystem` abstraction |

Override with `--scanner posix|macos|python`.

### Free-Threaded Python

dux supports free-threaded Python (3.13t+). Both C extensions (`_walker`, `_matcher`) declare `Py_MOD_GIL_NOT_USED`, enabling true parallel execution without GIL contention. Use `--verbose` to see GIL status and active scanner at runtime.

When the GIL is disabled, `default_scanner()` selects `PythonScanner` — the C `readdir` wrapper's overhead becomes negligible compared to the parallelism gains from true multi-threading, and the pure Python scanner has the advantage of working through the `FileSystem` abstraction layer.

### Pattern Matching

Pattern matching (insight generation) is the second-hottest path after scanning. dux avoids naive fnmatch-per-rule by classifying all 59 rules at compile time into fast string operations:

- **EXACT** — `dict` lookup on lowercased basename (`O(1)`)
- **CONTAINS** — Aho-Corasick automaton (C extension) for multi-pattern substring search in a single pass over the path
- **ENDSWITH / STARTSWITH** — simple `str.endswith` / `str.startswith` on the basename
- **GLOB** — fallback to `fnmatch` only for patterns that can't be decomposed

Brace expansion (`{a,b}`) is resolved at compile time. All matcher values are lowercased once at build time; paths are lowercased once per node for case-insensitive matching.

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
| Type checking | [basedpyright](https://docs.basedpyright.com/) (standard mode) |
| Linting/formatting | [Ruff](https://docs.astral.sh/ruff/) |
| Testing | [pytest](https://docs.pytest.org/) |
| Package management | [uv](https://docs.astral.sh/uv/) |

## License

MIT
