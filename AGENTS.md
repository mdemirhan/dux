# Agent Guidelines

Instructions for AI agents working on this codebase.

## Tooling

- Use `uv` for all operations:
  - `uv sync` to install dependencies
  - `uv run <command>` to run commands in the project environment
  - `uv add <package>` to add dependencies
- Use `ruff` for linting and formatting (`ruff check`, `ruff format`)
- Use `basedpyright` for type checking
- Use `pytest` for testing
- After making code changes, run `uv run ruff format`, `uv run ruff check`, and `uv run basedpyright` before considering work complete

## Architecture

```
csrc/
├── walker.c                    # C source: scan_dir_nodes (readdir), scan_dir_bulk_nodes (getattrlistbulk)
└── matcher.c                   # C source: Aho-Corasick automaton (trie + fail links + BFS)

dux/
├── _matcher.so / .pyi      # Compiled C extension: Aho-Corasick multi-pattern matcher
├── _walker.so / .pyi       # Compiled C extension: scan_dir_nodes (POSIX), scan_dir_bulk_nodes (macOS)
├── cli/app.py              # Entry point, CLI flags, progress display
├── ui/
│   ├── app.py              # TUI application (Textual), all views and keybindings
│   └── app.tcss            # Textual CSS styling (Tomorrow Night theme)
├── models/
│   ├── enums.py            # NodeKind (FILE/DIRECTORY), InsightCategory (TEMP/CACHE/BUILD_ARTIFACT)
│   ├── scan.py             # ScanNode, ScanStats, ScanSnapshot, ScanError, ScanResult
│   └── insight.py          # Insight, InsightBundle dataclasses
├── config/
│   ├── schema.py           # AppConfig, PatternRule dataclasses with to_dict/from_dict
│   ├── defaults.py         # 59 built-in pattern rules
│   └── loader.py           # JSON config loading with FileSystem abstraction
├── scan/
│   ├── __init__.py          # Scanner protocol, default_scanner() (GIL-aware selection)
│   ├── _base.py             # ThreadedScannerBase (thread pool + work queue)
│   ├── python_scanner.py    # Pure Python scanner using FileSystem.scandir()
│   ├── posix_scanner.py     # C extension scanner using _walker.scan_dir_nodes (readdir)
│   └── macos_scanner.py     # C extension scanner using _walker.scan_dir_bulk_nodes (getattrlistbulk)
└── services/
    ├── fs.py               # FileSystem protocol, OsFileSystem, DEFAULT_FS singleton
    ├── insights.py          # Pattern matching, per-category min-heaps for top-K
    ├── patterns.py          # Compiled matchers: EXACT, CONTAINS+ENDSWITH (Aho-Corasick), STARTSWITH, GLOB
    ├── tree.py              # Tree traversal: iter_nodes, top_nodes (heapq.nlargest), finalize_sizes
    ├── formatting.py        # format_bytes, relative_bar
    └── summary.py           # Non-interactive CLI summary rendering
```

### Data Flow

1. `cli/app.py` parses CLI args, loads config via `loader.py`, selects scanner via `default_scanner()`
2. The selected scanner (`PythonScanner`, `PosixScanner`, or `MacOSScanner`) walks the filesystem in parallel, builds `ScanSnapshot` (immutable tree of `ScanNode`)
3. `insights.py` walks the scan tree, matches against compiled patterns, produces `InsightBundle`
4. Either `summary.py` renders CLI output, or `ui/app.py` launches the interactive TUI

### Key Design Decisions

- **`ScanSnapshot` is immutable after scanning.** The scan tree never changes. All TUI views are read-only projections. Row caches are safe to keep across tab switches.
- **`Result[T, E]` for error handling.** Scanner and config loader return `Result` types. CLI/TUI boundary code unwraps them.
- **`FileSystem` protocol for testability.** `PythonScanner` and config loader accept a `fs` parameter (defaults to `DEFAULT_FS` singleton). Tests use `MemoryFileSystem` — no temp files, no disk I/O. Note: `PosixScanner` and `MacOSScanner` bypass `FileSystem` entirely, calling C extensions directly.
- **`DirEntry.stat` is bundled, not separate.** `OsFileSystem.scandir` calls `entry.stat(follow_symlinks=False)` on the `os.DirEntry` object (which uses OS-cached stat data) and bundles the result into each `DirEntry`. The scanner reads `entry.stat` directly — never calls `fs.stat()` per entry in the hot loop.
- **GIL-aware scanner selection.** `default_scanner()` picks the best backend: `MacOSScanner` on macOS (uses `getattrlistbulk` — single syscall per directory batch), `PosixScanner` when GIL is enabled (C `readdir`, benefits from GIL release during I/O), `PythonScanner` when GIL is disabled (true parallelism makes C overhead negligible).

## Performance-Critical Code

**Any change to `scan/`, `services/fs.py`, or `services/patterns.py` that could affect scanning or pattern matching performance must be flagged to the user before implementation.**

### General Constraints

- **Use `os.DirEntry.stat()` for cached stat.** The OS caches stat info from `readdir`/`getdents` syscalls. Calling `os.stat(path)` separately per file is an extra syscall per entry — a major regression at millions of files.
- **`scandir` uses a generator (yield).** cProfile exaggerates generator overhead due to per-call instrumentation. Real-world wall-clock benchmarks show generators are ~4% faster than list materialization (avoid per-directory list allocation). Always benchmark with `time.perf_counter`, not cProfile, for wall-clock comparisons.
- **Avoid `Path` object creation in hot loops.** The `FileSystem` protocol uses `str` for all paths. `Path` objects are only created inside `OsFileSystem` methods, never in scanner loop code.
- **`DirEntry` and `StatResult` are frozen dataclasses with `__slots__`.** Minimize per-entry allocation overhead.
- **The scan tree is I/O-bound.** On a 2.1M file scan (~22s), stat syscalls and `posix.scandir` dominate. The abstraction layer adds zero measurable overhead vs. direct `os.scandir` calls.

### C Extensions (`csrc/`)

Two C extensions accelerate the two hottest paths: directory scanning and pattern matching. Both declare `Py_MOD_GIL_NOT_USED` for free-threaded Python compatibility.

**`dux._walker`** (`csrc/walker.c`) — Directory scanning with GIL released during I/O:

- `scan_dir_nodes()` — Uses POSIX `opendir`/`readdir`/`lstat`. Collects entries into a C-level `EntryBuf` (heap-allocated array) while the GIL is released (`Py_BEGIN_ALLOW_THREADS`), then re-acquires the GIL to build `ScanNode` Python objects and append them to `parent.children`. This avoids per-entry GIL acquire/release overhead.
- `scan_dir_bulk_nodes()` — macOS only. Uses `getattrlistbulk`, which returns name + type + size + alloc-size for all entries in a single syscall per buffer-full (256 KB buffer). Same two-phase pattern: GIL-free I/O fill, then GIL-held node construction.

**`dux._matcher`** (`csrc/matcher.c`) — Aho-Corasick automaton for multi-pattern substring matching:

- Custom trie with BFS-constructed fail links and dictionary suffix links.
- 256-wide child array per node for full byte-range UTF-8 safety.
- Build once (`add_word` + `make_automaton`), then `iter()` is read-only — inherently thread-safe for concurrent readers.
- Used by `patterns.py` to match all CONTAINS and ENDSWITH patterns in a single linear pass over each path string, replacing O(patterns × path_length) with O(path_length + matches).

### Scanner Backends (`dux/scan/`)

Three scanner implementations share `ThreadedScannerBase` (thread pool + `_WorkQueue`):

| Scanner | When selected | How it works |
|---------|---------------|--------------|
| `MacOSScanner` | macOS (default) | Calls `_walker.scan_dir_bulk_nodes` — `getattrlistbulk` fetches all entries + stat in one syscall per batch. Fastest on macOS (fewer syscalls than readdir+lstat). |
| `PosixScanner` | Linux with GIL enabled | Calls `_walker.scan_dir_nodes` — C `readdir` + `lstat` with GIL released during I/O. Benefits from GIL release allowing other threads to run during I/O waits. |
| `PythonScanner` | Fallback / GIL disabled | Uses `self._fs.scandir()` (pure Python). Only scanner that works with the `FileSystem` abstraction (and thus `MemoryFileSystem` for testing). Selected when GIL is disabled because true parallelism makes the C overhead negligible. |

**`_WorkQueue`** uses a `deque` + single `Condition` + counter-based completion (`_outstanding` + `_done` Event). This is lighter than `queue.Queue` (which uses 3 internal locks). Workers batch-flush local stat counters to reduce lock contention.

**Important:** `PosixScanner` and `MacOSScanner` bypass `self._fs` entirely — they call C extensions directly. Only `PythonScanner` goes through the `FileSystem` protocol. Scanner tests that need the `MemoryFileSystem` must use `PythonScanner`.

### Pattern Matching Optimizations (`services/patterns.py`)

Pattern matching runs once per node during insight generation — millions of calls on large trees. Key optimizations:

**1. Compile-time classification (`_classify`):** Each pattern rule is decomposed at startup into the fastest possible string operation:

| Pattern shape | Matcher kind | Runtime operation |
|---------------|-------------|-------------------|
| `**/name` | `EXACT` | `dict.get(basename)` — O(1) |
| `**/segment/**` | `CONTAINS` | Aho-Corasick automaton scan |
| `**/*.ext` | `ENDSWITH` | Aho-Corasick automaton (end-only key) |
| `**/prefix*` | `STARTSWITH` | `basename.startswith(prefix)` |
| Everything else | `GLOB` | `fnmatch` fallback |

Only patterns that truly need globbing fall through to `fnmatch`. In practice, very few rules hit the GLOB path.

**2. Brace expansion at compile time:** `_expand_braces()` resolves `{a,b,c}` patterns recursively, so the hot loop never sees brace syntax.

**3. Case-insensitive matching without re-lowering:** All matcher values are lowercased at compile time. Paths are lowercased once per node (in `insights.py`), then the pre-lowered values are compared directly.

**4. Aho-Corasick for CONTAINS + ENDSWITH patterns:** Instead of checking each pattern individually (`O(patterns × path_length)`), all CONTAINS and ENDSWITH needles are loaded into a single C-level Aho-Corasick automaton. `ac.iter(lpath)` finds all matches in one linear scan (`O(path_length + matches)`). CONTAINS patterns produce two AC keys: a substring variant (`/segment/`, match anywhere) and an end-of-string variant (`/segment`, end-only). ENDSWITH patterns produce one end-only key (e.g., `.log`). Since `lpath` always ends with the basename, `end_idx == len(lpath) - 1` is equivalent to `basename.endswith(suffix)`.

**5. File/dir split at compile time (`CompiledRuleSet`):** Rules with `apply_to=FILE` only go in `for_file`, `apply_to=DIR` only in `for_dir`, and `BOTH` goes in both. The hot loop selects `bk = rs.for_dir if is_dir else rs.for_file` once per node — no per-pattern `apply_to` branching.

**6. Integer kind dispatch:** Matcher kinds are plain integers (`_CONTAINS = 0`, `_ENDSWITH = 1`, etc.) rather than enums, avoiding enum attribute lookup overhead in the hot loop.

**7. Inline loops in `match_all`:** All matching uses explicit `for` loops instead of list comprehensions to avoid allocating ~10 temporary lists per call (millions of calls).

**8. First-match-per-category dedup:** `_try()` uses a `set[str]` of seen category values to stop after the first match per category, avoiding redundant work.

### Benchmarking Protocol

When evaluating performance changes:

1. Always use `time.perf_counter` for wall-clock timing, not `cProfile`
2. Run a warm-up pass first (populates OS caches)
3. Run at least 5 timed iterations and report avg + best
4. Test on both small (~300k files) and large (~2M files) directories
5. Compare against the baseline in the same session to control for I/O variance

## Error Handling

- Use the `result` library (`Result`, `Ok`, `Err`) for operation outcomes
- Keep pure/service functions returning `Result` for expected failure paths
- Keep boundary layers (CLI/TUI) imperative and explicit
- Avoid tuple-based `(value, warning)` return patterns
- Avoid custom ad-hoc success/failure unions when `Result` is appropriate

## Testing

- Tests use `MemoryFileSystem` from `tests/fs_mock.py` — no `tmp_path`, no disk I/O
- Scanner tests run with `workers=1` for deterministic ordering
- Pattern: `add_dir`/`add_file` builder methods return `self` for chaining
- `DirEntry.stat=None` simulates access errors (stat failures)
- Always run `uv run pytest -x -q` after changes

## Platform

- macOS and Linux only
- No Windows support
- Symlinks are not followed (`follow_symlinks=False` throughout)
